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
                    UncertainSend::AskAgent => {
                        if !possibly_sent {
                            // Give the agent a moment to register the request.
                            backoff.wait().await;
                        }
                        return self.ask_agent(operation, request_id).await;
                    }
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
            // The request is loaded first: a request that is gone fails the
            // call cleanly from "claimed", and a later holder that finds the
            // same call again reports the same error and finishes it.
            let Some(payload) = self.load(&request).await? else {
                if uncertain {
                    // It may have been sent earlier and cannot be sent again.
                    self.mark_unresolved(UnresolvedKind::Uncertain, "request_unavailable").await;
                    return Ok(());
                }
                return self
                    .end_with_error("failed", "request_unavailable", FinalDispatchOutcome::Failed, "request_unavailable")
                    .await;
            };
            // Send intent is durable before the network call.
            held_write!(self, "send intent", self.held.begin_send(operation.clone()));
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
                // The answer may already carry the task's state, even its
                // result: record the acceptance, then report that state
                // before deciding whether the task must be followed at all.
                QueryOutcome::Found { remote, status, snapshot } => {
                    let evidence = AcceptanceEvidence { operation, remote };
                    let cancel = held_write!(self, "acceptance", self.held.record_acceptance(evidence.clone()));
                    self.report_task_state(status, snapshot).await?;
                    if !self.ended && !self.awaiting_input {
                        self.reattach().await?;
                    }
                    if cancel.is_some() && !self.ended {
                        let view = self.held.view().clone();
                        self.carry_out_cancel(&view).await?;
                    }
                    return Ok(());
                }
                QueryOutcome::Unsupported => {
                    self.mark_unresolved(UnresolvedKind::Uncertain, "query_by_request_unsupported").await;
                    return Ok(());
                }
                // "Not found" may only mean "not registered yet": ask a few
                // times, and even then it is no proof, so the call is left
                // unresolved rather than sent again.
                outcome @ (QueryOutcome::NotFound | QueryOutcome::Unavailable { .. }) => {
                    tries += 1;
                    if tries > self.config.not_sent_retries {
                        let code = match outcome {
                            QueryOutcome::Unavailable { code } => code,
                            _ => "not_found_by_request".to_owned(),
                        };
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
            Reattach::Unresolved => self.mark_blind("cannot_observe_remote_task").await,
        }
        Ok(())
    }

    /// Reports the state of the remote task as read by a query, unless it is
    /// the state that was reported last.
    pub async fn report_task_state(&mut self, status: TaskStatus, snapshot: Payload) -> Flow {
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
        // The state word the rules read comes from the status, not from
        // whatever shape the snapshot has.
        let snapshot = with_state(snapshot, status);
        // The end comes from the status: a snapshot stored by reference
        // carries no state word the rules could read.
        let end = match status {
            TaskStatus::Completed => Some(FinalDispatchOutcome::Completed),
            TaskStatus::Failed | TaskStatus::Rejected => Some(FinalDispatchOutcome::Failed),
            TaskStatus::Canceled => Some(FinalDispatchOutcome::Canceled),
            _ => None,
        };
        self.on_event_ending(kind, None, snapshot, end).await
    }

    /// Reads the remote task once and reports its state when it changed.
    pub async fn poll_remote(&mut self) -> Flow {
        self.read_remote().await.map(|_| ())
    }

    /// `poll_remote`, telling the caller what the read answered.
    pub async fn read_remote(&mut self) -> Flow<RemoteRead> {
        let target = self.spec().target.clone();
        let Some(remote) = self.held.view().execution.remote.clone() else {
            return Ok(RemoteRead::Gone);
        };
        match self.ports.agent.query(&target, RemoteQuery::ByTask(remote)).await {
            QueryOutcome::Found { status, snapshot, .. } => {
                self.report_task_state(status, snapshot).await?;
                Ok(RemoteRead::Found)
            }
            QueryOutcome::Unsupported if self.polling => {
                self.mark_blind("query_unsupported").await;
                Ok(RemoteRead::Gone)
            }
            QueryOutcome::NotFound | QueryOutcome::Unsupported => {
                tracing::warn!(did = %self.did().0, "remote task not readable");
                Ok(RemoteRead::Gone)
            }
            // Not an agent result: ask again on the next tick.
            QueryOutcome::Unavailable { code } => {
                tracing::debug!(did = %self.did().0, code, "remote task query unavailable");
                Ok(RemoteRead::Unavailable)
            }
        }
    }
}

/// What one read of the remote task answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemoteRead {
    /// The task was read; its state was reported unless it was a repeat.
    Found,
    /// A definite "no such task" or "no task read": asking again cannot help.
    Gone,
    /// The read failed this time.
    Unavailable,
}

/// Ensures a terminal or question payload carries the state word of `status`.
fn with_state(snapshot: Payload, status: TaskStatus) -> Payload {
    let word = match status {
        TaskStatus::Submitted | TaskStatus::Working => return snapshot,
        TaskStatus::InputRequired => "input_required",
        TaskStatus::AuthRequired => "auth_required",
        TaskStatus::Completed => "completed",
        TaskStatus::Failed => "failed",
        TaskStatus::Canceled => "canceled",
        TaskStatus::Rejected => "rejected",
    };
    match snapshot {
        Payload::Inline(serde_json::Value::Object(mut object)) => {
            object.insert("state".to_owned(), serde_json::Value::String(word.to_owned()));
            Payload::Inline(serde_json::Value::Object(object))
        }
        Payload::Inline(other) => Payload::Inline(serde_json::json!({ "state": word, "task": other })),
        by_reference => by_reference,
    }
}
