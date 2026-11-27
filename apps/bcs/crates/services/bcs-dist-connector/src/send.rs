//! Starting a call, resolving a start whose outcome is unknown, and
//! (re-)attaching to the remote task.
//!
//! SENT policy: the current agent port does not provide trusted downstream
//! dedupe evidence, so a possibly-sent start is never repeated. It can be
//! looked up by request id, or retained by the verification lane. Only a
//! definite NotSent is retried in the ordinary lane.
//!
//! Glossary:
//! - **send intent**: `begin_send(Start)` moves the phase `claimed →
//!   `sending` and durably writes the attempt's `SendProof` before the
//!   network call (B3, plan T6). A new holder that finds `sending` cannot
//!   know whether the start reached the agent and must **resolve the
//!   start** (`start(true)`) with the SENT policy (B15).
//! - **acceptance**: `record_acceptance` stores the agent's task reference
//!   and moves the phase to `accepted` before any event is reported (B4).
//! - **re-attach**: follow an accepted task again (after a takeover, a
//!   resume, or a broken stream) by the mode the capabilities allow.
//!
//! Re-attaching can repeat intermediate events: the read-first query returns
//! a snapshot, and a re-subscribe has no replay position, so content the
//! former holder already reported may come again. Intermediate events get new
//! `(did, holder_epoch, seq)` ids under the new epoch, so both copies are kept
//! (best effort, ordered per epoch); key events are deduplicated by their
//! stable id.

use crate::call::{Call, Flow, held_write};
use crate::delayed::UnresolvedKind;
use crate::report::Admitted;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::Reattach;
use bcs_service_api::port::dist_agent::{CallOperation, StableSendRequest};
use bcs_service_api::port::dist_agent::{QueryOutcome, RemoteEventStream, RemoteQuery, SendOutcome};
use bcs_service_api::port::repo::distributed::dispatch::{AcceptanceEvidence, SendOperation};

/// The canonical body bytes of a send attempt: the inline JSON as sent, or
/// the immutable blob key for a request stored by reference. A resend's
/// proof covers the same bytes, so a drifted request never matches.
pub(crate) fn canonical_body(payload: &Payload) -> Vec<u8> {
    match payload {
        Payload::Inline(value) => serde_json::to_vec(value).unwrap_or_default(),
        Payload::Ref(reference) => reference.key.clone().into_bytes(),
    }
}

impl Call {
    /// Sends the call's request. `possibly_sent` = the dispatch was found in
    /// the sending phase, so an earlier holder may already have sent it.
    ///
    /// A former holder's send intent is possibly sent, even if the old
    /// record has no proof or the payload cannot be loaded. Recovery uses
    /// only the original request id and never sends its body again.
    pub async fn start(&mut self, possibly_sent: bool) -> Flow {
        let did = self.did().clone();
        let spec = self.spec().clone();
        let capabilities = spec.capabilities.clone();
        let request_id = self.ports.rules.start_request_id(&did);
        let operation = SendOperation::Start {
            request_id: request_id.clone(),
        };
        if possibly_sent {
            return self.resolve_start(operation, request_id, "start_maybe_sent").await;
        }
        let proof_operation = ProofOperation::Start {
            request_id: request_id.clone(),
        };
        let mut not_sent = 0;
        let mut backoff = self.backoff();
        let Some(payload) = self.load(&spec.request).await? else {
            return self
                .end_with_error("failed", "request_unavailable", FinalDispatchOutcome::Failed, "request_unavailable")
                .await;
        };
        // Send intent and its identity are durable before any network call.
        let proof = self.ports.rules.send_proof(
            &spec,
            proof_operation,
            &canonical_body(&payload),
            self.config.send_fingerprint(&capabilities),
        );
        loop {
            let view = held_write!(self, "dispatch read", self.held.refresh());
            if view.control.cancel.is_some() {
                return self.end_canceled_unsent().await;
            }
            held_write!(
                self,
                "send intent",
                self.held.begin_send(operation.clone(), proof.clone())
            );
            let authority = self.authority_now(CallOperation::Start, None, &spec);
            let send = StableSendRequest {
                request_id: request_id.clone(),
                context_id: None,
                payload: payload.clone(),
                node: spec.node.clone(),
                attempt: spec.attempt,
                frozen_history: None,
            };
            match self.ports.agent.start(&authority, send).await {
                SendOutcome::Accepted { remote, events } => {
                    return self.accepted(operation, remote, events).await;
                }
                SendOutcome::Completed { result } => {
                    return match self.ports.rules.completed_result(&result) {
                        Ok(verified) => {
                            if self.report(AgentEventKind::Final, None, verified).await? == Admitted::Yes {
                                self.finish(FinalDispatchOutcome::Completed, "sync_result").await?;
                            }
                            Ok(())
                        }
                        Err(code) => {
                            self.end_with_error("failed", code, FinalDispatchOutcome::Failed, code).await
                        }
                    };
                }
                SendOutcome::Rejected { code } => {
                    let evidence = format!("rejected:{code}");
                    return self
                        .end_with_error("rejected", &code, FinalDispatchOutcome::Failed, &evidence)
                        .await;
                }
                SendOutcome::NotSent { code } => {
                    not_sent += 1;
                    if not_sent > self.config.not_sent_retries {
                        self.mark_unresolved(UnresolvedKind::NotSent, &code).await;
                        return Ok(());
                    }
                    backoff.wait().await;
                }
                SendOutcome::OutcomeUnknown { code } => {
                    tracing::warn!(did = %did.0, code, "start outcome unknown");
                    if capabilities.query_by_request {
                        // Give the index time to register the request.
                        backoff.wait().await;
                    }
                    return self.resolve_start(operation, request_id, &code).await;
                }
            }
        }
    }

    async fn resolve_start(&mut self, operation: SendOperation, request_id: RequestId, code: &str) -> Flow {
        if self.spec().capabilities.query_by_request {
            self.ask_agent(operation, request_id).await
        } else {
            self.hand_off(code, VerificationKey::Retained).await
        }
    }

    /// Asks the agent whether it knows the request (no resend). Found =
    /// the start did reach it: record the acceptance and go on as if the
    /// start had just been accepted.
    async fn ask_agent(&mut self, operation: SendOperation, request_id: RequestId) -> Flow {
        let mut backoff = self.backoff();
        let mut tries = 0u32;
        loop {
            let authority = self.authority_now(CallOperation::Query, None, self.spec());
            match self.ports.agent.query(&authority, RemoteQuery::ByRequest(request_id.clone())).await {
                // The answer may already carry the task's state, even its
                // result: record the acceptance, then report that state
                // before deciding whether the task must be followed at all.
                QueryOutcome::Found { remote, status, snapshot } => {
                    if let Some(known) = self.held.view().execution.remote.clone() {
                        if !self.owns(&remote, &known) {
                            // A task already accepted by this call: an
                            // answer about any other task is not evidence.
                            tracing::warn!(
                                did = %self.did().0, task = %remote.task_id,
                                "by-request answer of a foreign task ignored"
                            );
                            tries += 1;
                            if tries > self.config.not_sent_retries {
                                return self
                                    .hand_off("remote_ref_mismatch", VerificationKey::ByRequest { request_id })
                                    .await;
                            }
                            backoff.wait().await;
                            continue;
                        }
                    }
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
                    // The standard profile keeps this unknown a verification
                    // obligation (plan T7): never a resend, never a recovery.
                    return self
                        .hand_off("query_by_request_unsupported", VerificationKey::Retained)
                        .await;
                }
                // "Not found" may only mean "not registered yet": ask a few
                // times, and even then it is no proof. The short-term query
                // capacity is exhausted: hand the call to the verification
                // lane (plan T7) instead of parking it in the actor forever.
                outcome @ (QueryOutcome::NotFound | QueryOutcome::Unavailable { .. }) => {
                    tries += 1;
                    if tries > self.config.not_sent_retries {
                        let code = match outcome {
                            QueryOutcome::Unavailable { code } => code,
                            _ => "not_found_by_request".to_owned(),
                        };
                        return self
                            .hand_off(&code, VerificationKey::ByRequest { request_id })
                            .await;
                    }
                    backoff.wait().await;
                }
            }
        }
    }

    /// The agent returned its task: make that durable before any event is
    /// reported, and act at once on a cancel that arrived meanwhile.
    ///
    /// `record_acceptance` returns the Worker's cancel as read in the same
    /// store round: a cancel written while the start was in flight. It is
    /// carried out at once instead of waiting for the next tick. Without a
    /// stream from the start, the actor re-attaches right away.
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
    ///
    /// The mode comes from `rules::reattach` over `/spec/capabilities`
    /// (`DownstreamCapabilities`, written by the Worker):
    /// - subscribe: open a new event stream (after one task read when the
    ///   agent can be queried by task); a failed subscribe is tried again on
    ///   the next tick;
    /// - poll: set `polling`, every tick then reads the task;
    /// - neither: the actor goes blind.
    ///
    /// With no stored remote task id there is nothing to follow: the call is
    /// marked unresolved.
    pub async fn reattach(&mut self) -> Flow {
        self.reattach_due = false;
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
                let authority = self.authority_now(CallOperation::Subscribe, Some(remote.clone()), self.spec());
                match self.ports.agent.subscribe(&authority, &remote, None).await {
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
    /// the state that was reported last (`last_polled`).
    ///
    /// The task status becomes an event kind: submitted/working → snapshot
    /// (intermediate); input/auth-required → a question (key); completed →
    /// final; failed/canceled/rejected → error (B10). Terminal events then
    /// end the call through `on_event`.
    pub async fn report_task_state(&mut self, status: TaskStatus, snapshot: Payload) -> Flow {
        if self.last_polled.as_ref() == Some(&(status, snapshot.clone())) {
            return Ok(());
        }
        self.last_polled = Some((status, snapshot.clone()));
        // The interaction ids are placeholders; the rules derive them.
        let placeholder = || InteractionId(String::new());
        // The Completed snapshot is not the product yet: only the rules'
        // verified completion turns it into the call's Final, and an
        // incomplete one is a durable stop fact the recovery continues
        // from (plan T6).
        let kind = match status {
            TaskStatus::Submitted | TaskStatus::Working => AgentEventKind::Snapshot,
            TaskStatus::InputRequired => AgentEventKind::InputRequired {
                interaction: placeholder(),
            },
            TaskStatus::AuthRequired => AgentEventKind::AuthRequired {
                interaction: placeholder(),
            },
            TaskStatus::Completed => {
                let snapshot = with_state(snapshot, status);
                // The Completed snapshot becomes the product Final only when
                // the rules verify it complete; an incomplete one is a
                // durable stop fact the recovery continues from (plan T6).
                return match self.ports.rules.completed_result(&snapshot) {
                    Ok(verified) => {
                        let known = self.held.view().execution.remote.clone();
                        self.on_event(AgentEventKind::Final, known, None, verified).await
                    }
                    Err(code) => self.stop_fact(code, snapshot).await,
                };
            }
            TaskStatus::Failed | TaskStatus::Canceled | TaskStatus::Rejected => AgentEventKind::Error,
        };
        // The state word the rules read comes from the status, not from
        // whatever shape the snapshot has.
        let snapshot = with_state(snapshot, status);
        let known = self.held.view().execution.remote.clone();
        self.on_event(kind, known, None, snapshot).await
    }

    /// Reads the remote task once (query by task id) and reports its state
    /// when it changed. Used by polling, by the read-first re-attach, and
    /// while waiting for a reply.
    pub async fn poll_remote(&mut self) -> Flow {
        self.read_remote().await.map(|_| ())
    }

    /// `poll_remote`, telling the caller what the read answered. A query
    /// that is unsupported while polling makes the actor blind; an
    /// unavailable agent is not an agent result and is simply asked again
    /// later.
    pub async fn read_remote(&mut self) -> Flow<RemoteRead> {
        let remote = self.held.view().execution.remote.clone();
        let Some(remote) = remote else {
            return Ok(RemoteRead::Gone);
        };
        let authority = self.authority_now(CallOperation::Query, Some(remote.clone()), self.spec());
        match self.ports.agent.query(&authority, RemoteQuery::ByTask(remote)).await {
            QueryOutcome::Found { remote, status, snapshot } => {
                if let Some(known) = self.held.view().execution.remote.clone() {
                    if !self.owns(&remote, &known) {
                        // Evidence of another task is never this call's stop
                        // fact (plan T6): it answers nothing about A.
                        tracing::warn!(
                            did = %self.did().0, task = %remote.task_id,
                            "query answered with a foreign task; not saved"
                        );
                        return Ok(RemoteRead::Unavailable);
                    }
                }
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
///
/// The rules read the `state` field to build the key event's digest and to
/// tell canceled from failed. Examples:
///
/// ```text
/// Canceled, {"id": "t1"}         → {"id": "t1", "state": "canceled"}
/// Failed,   "oops"               → {"state": "failed", "task": "oops"}
/// Completed, Ref(blob)           → {"state": "completed", "task_ref": <blob>}
/// Working,  anything             → unchanged (intermediate snapshot)
/// ```
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
        // A snapshot stored by reference carries no state word: wrap it, so
        // the event itself says how the call ended.
        Payload::Ref(reference) => Payload::Inline(serde_json::json!({ "state": word, "task_ref": reference })),
    }
}
