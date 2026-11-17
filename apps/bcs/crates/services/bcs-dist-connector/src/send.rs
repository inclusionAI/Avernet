//! Starting a call, resolving a start whose outcome is unknown, and
//! (re-)attaching to the remote task.
//!
//! SENT policy: a start that may have reached the agent is never repeated
//! blindly. It is resent only when the agent declares the start idempotent,
//! asked for by request id when the agent supports that, and otherwise left
//! unresolved. A start that definitely did not leave the process is retried a
//! bounded number of times.

use crate::call::{Call, Flow, held_write};
use crate::delayed::UnresolvedKind;
use crate::report::Admitted;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{Reattach, UncertainSend};
use bcs_service_api::port::dist_agent::{
    QueryOutcome, RemoteEventStream, RemoteQuery, SendOutcome, StableSendRequest,
};
use bcs_service_api::port::repo::distributed::dispatch::{AcceptanceEvidence, SendOperation};

impl Call {
    /// Sends the call's request. `possibly_sent` = the dispatch was found in
    /// the sending phase, so an earlier holder may already have sent it.
    pub async fn start(&mut self, possibly_sent: bool) -> Flow {
        let did = self.did().clone();
        let target = self.spec().target.clone();
        let capabilities = self.spec().capabilities.clone();
        let request = self.spec().request.clone();
        let request_id = self.ports.rules.start_request_id(&did);
        let operation = SendOperation::Start {
            request_id: request_id.clone(),
        };
        let retries = self.config.not_sent_retries;
        let (mut uncertain, mut not_sent, mut resends) = (possibly_sent, 0u32, 0u32);
        let mut backoff = self.backoff();
        loop {
            if uncertain {
                match self.ports.rules.uncertain_start(&capabilities) {
                    UncertainSend::Resend if resends < retries => resends += 1,
                    UncertainSend::Resend => {
                        self.mark_unresolved(UnresolvedKind::Uncertain, "resend_exhausted").await;
                        return Ok(());
                    }
                    UncertainSend::AskAgent => return self.ask_agent(operation, request_id).await,
                    UncertainSend::Unresolved => {
                        self.mark_unresolved(UnresolvedKind::Uncertain, "start_outcome_unknown").await;
                        return Ok(());
                    }
                }
            } else {
                // Nothing has left the process yet: a cancel ends the call here.
                let view = held_write!(self, "dispatch read", self.held.refresh());
                if view.control.cancel.is_some() {
                    return self.end_canceled_unsent().await;
                }
            }
            // Send intent is durable before the network call.
            held_write!(self, "send intent", self.held.begin_send(operation.clone()));
            let Some(payload) = self.load(&request).await? else {
                if uncertain {
                    self.mark_unresolved(UnresolvedKind::Uncertain, "request_unavailable").await;
                    return Ok(());
                }
                return self
                    .end_with_error("failed", "request_unavailable", FinalDispatchOutcome::Failed, "request_unavailable")
                    .await;
            };
            let send = StableSendRequest {
                request_id: request_id.clone(),
                context_id: None,
                payload,
            };
            match self.ports.agent.start(&target, send).await {
                SendOutcome::Accepted { remote, events } => {
                    return self.accepted(operation, remote, events).await;
                }
                SendOutcome::Completed { result } => {
                    if self.report(AgentEventKind::Final, None, result).await? == Admitted::Yes {
                        self.finish(FinalDispatchOutcome::Completed, "sync_result").await?;
                    }
                    return Ok(());
                }
                SendOutcome::Rejected { code } => {
                    let evidence = format!("rejected:{code}");
                    return self
                        .end_with_error("rejected", &code, FinalDispatchOutcome::Failed, &evidence)
                        .await;
                }
                SendOutcome::NotSent { code } => {
                    not_sent += 1;
                    if not_sent > retries {
                        let kind = if uncertain {
                            UnresolvedKind::Uncertain
                        } else {
                            UnresolvedKind::NotSent
                        };
                        self.mark_unresolved(kind, &code).await;
                        return Ok(());
                    }
                    backoff.wait().await;
                }
                SendOutcome::OutcomeUnknown { code } => {
                    tracing::warn!(did = %did.0, code, "start outcome unknown");
                    if !uncertain {
                        uncertain = true;
                    } else {
                        backoff.wait().await;
                    }
                }
            }
        }
    }

    /// Asks the agent whether it knows the request (no resend).
    async fn ask_agent(&mut self, operation: SendOperation, request_id: RequestId) -> Flow {
        let target = self.spec().target.clone();
        let mut backoff = self.backoff();
        let mut tries = 0u32;
        loop {
            match self.ports.agent.query(&target, RemoteQuery::ByRequest(request_id.clone())).await {
                QueryOutcome::Found { remote, .. } => return self.accepted(operation, remote, None).await,
                QueryOutcome::NotFound | QueryOutcome::Unsupported => {
                    // "Not found" is no proof the agent will not process it.
                    self.mark_unresolved(UnresolvedKind::Uncertain, "not_found_by_request").await;
                    return Ok(());
                }
                QueryOutcome::Unavailable { code } => {
                    tries += 1;
                    if tries > self.config.not_sent_retries {
                        self.mark_unresolved(UnresolvedKind::Uncertain, &code).await;
                        return Ok(());
                    }
                    backoff.wait().await;
                }
            }
        }
    }

    /// The agent returned its task: make that durable before any event is
    /// reported, and act at once on a cancel that arrived meanwhile.
    async fn accepted(
        &mut self,
        operation: SendOperation,
        remote: RemoteTaskRef,
        events: Option<RemoteEventStream>,
    ) -> Flow {
        let evidence = AcceptanceEvidence { operation, remote };
        let cancel = held_write!(self, "acceptance", self.held.record_acceptance(evidence.clone()));
        match events {
            Some(stream) => self.events = Some(stream),
            None => self.reattach().await?,
        }
        if cancel.is_some() && !self.ended {
            let view = self.held.view().clone();
            self.carry_out_cancel(&view).await?;
        }
        Ok(())
    }

    /// Follows an accepted remote task again: after a takeover, a resume, or
    /// a stream that broke. The declared capabilities decide how.
    pub async fn reattach(&mut self) -> Flow {
        self.reattach_due = false;
        let target = self.spec().target.clone();
        let capabilities = self.spec().capabilities.clone();
        let Some(remote) = self.held.view().execution.remote.clone() else {
            self.mark_unresolved(UnresolvedKind::Uncertain, "no_remote_task").await;
            return Ok(());
        };
        match self.ports.rules.reattach(&capabilities) {
            Reattach::Subscribe { query_first } => {
                if query_first {
                    // The current state first: it covers what the former
                    // holder may not have reported, under the same identities.
                    self.poll_remote().await?;
                    if self.ended || self.awaiting_input {
                        return Ok(());
                    }
                }
                match self.ports.agent.subscribe(&target, &remote, None).await {
                    Ok(stream) => self.events = Some(stream),
                    Err(error) => {
                        tracing::warn!(did = %self.did().0, code = error.code, "subscribe failed; next tick");
                        self.reattach_due = true;
                    }
                }
            }
            Reattach::Poll => self.polling = true,
            Reattach::Unresolved => {
                self.mark_unresolved(UnresolvedKind::Uncertain, "cannot_observe_remote_task").await;
            }
        }
        Ok(())
    }

    /// Reads the remote task once and reports its state when it changed.
    pub async fn poll_remote(&mut self) -> Flow {
        let target = self.spec().target.clone();
        let Some(remote) = self.held.view().execution.remote.clone() else {
            return Ok(());
        };
        match self.ports.agent.query(&target, RemoteQuery::ByTask(remote)).await {
            QueryOutcome::Found { status, snapshot, .. } => {
                if self.last_polled.as_ref() == Some(&(status, snapshot.clone())) {
                    return Ok(());
                }
                self.last_polled = Some((status, snapshot.clone()));
                // The interaction ids are placeholders; the rules derive them.
                let placeholder = || InteractionId(String::new());
                let kind = match status {
                    TaskStatus::Submitted | TaskStatus::Working => AgentEventKind::Snapshot,
                    TaskStatus::InputRequired => AgentEventKind::InputRequired {
                        interaction: placeholder(),
                    },
                    TaskStatus::AuthRequired => AgentEventKind::AuthRequired {
                        interaction: placeholder(),
                    },
                    TaskStatus::Completed => AgentEventKind::Final,
                    TaskStatus::Failed | TaskStatus::Canceled | TaskStatus::Rejected => AgentEventKind::Error,
                };
                self.on_event(kind, None, snapshot).await
            }
            QueryOutcome::Unsupported if self.polling => {
                self.polling = false;
                self.mark_unresolved(UnresolvedKind::Uncertain, "query_unsupported").await;
                Ok(())
            }
            QueryOutcome::NotFound | QueryOutcome::Unsupported => {
                tracing::warn!(did = %self.did().0, "remote task not readable");
                Ok(())
            }
            // Not an agent result: ask again on the next tick.
            QueryOutcome::Unavailable { code } => {
                tracing::debug!(did = %self.did().0, code, "remote task query unavailable");
                Ok(())
            }
        }
    }
}
