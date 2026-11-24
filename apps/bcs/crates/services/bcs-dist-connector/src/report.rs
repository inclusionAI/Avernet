//! Reporting agent events to the session inbox.
//!
//! Key events are admitted under their stable identity; intermediate events
//! are appended. Both are retried with the same identity until they land or
//! the holder is lost. A transport problem is never turned into an agent event.
//!
//! Glossary and consequences:
//! - **Key event** (final, error, input-required, auth-required): admitted
//!   with `admit_connector` under an input id `ae:<hash>` that has no holder
//!   epoch and no seq (B8). A later holder that re-reports the same event gets
//!   the same id, and the inbox answers with the same success (B11): this is
//!   what makes "admit, then crash before finish" safe (B5).
//! - **Intermediate event** (delta, thinking, tool, snapshot): appended with
//!   `append_stream`, identified by `(did, holder_epoch, seq)` (B7, B9).
//!   Retrying the same append repeats the same triple, and the Worker drops
//!   the repeat.
//! - **seq restarts at 1 per claim**: `Call::new` makes a fresh cursor for
//!   the new holder epoch. So seq values repeat across generations and only
//!   `(holder_epoch, seq)` orders events; and when a new holder re-reports
//!   content the former holder already appended (a re-attach can repeat it),
//!   the copies have different ids and both are kept (best effort, ordered per
//!   epoch). Key events have no such duplicates.
//! - **Not fenced by `disp`**: inbox appends and admissions are not checked
//!   against the dispatch version. A former actor that has not yet noticed it
//!   lost the call can still append; its events carry its old epoch. Between
//!   retries this module re-reads `disp` (`still_holder`) so a lost holder
//!   stops retrying.
//! - **Blobs**: a payload above `max_inline_bytes` is stored as an immutable
//!   blob keyed by its content hash and reported by reference. Identity and
//!   digest are computed from the inline payload first, so where it ends up
//!   stored does not change them.

use crate::call::{Call, Flow, Stop, held_write};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::Report;
use bcs_service_api::port::repo::distributed::common::IdempotentWrite;
use bcs_service_api::port::repo::distributed::dispatch::HolderError;
use bcs_service_api::port::repo::distributed::inbox::{
    AdmitOutcome, AppendAgentEvent, ConnectorAdmission,
};
use sha2::{Digest as _, Sha256};

/// How an admission ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Admitted {
    /// Landed (or was there already), or the session is quarantined: the
    /// caller may go on, e.g. finish the call after a terminal event.
    Yes,
    /// Another content was admitted under the same identity. Cannot happen
    /// with the payload-free digest; treated as a bug, the call is not ended.
    Conflict,
}

impl Call {
    /// Reports one remote event and, when it is terminal, finishes the call
    /// (terminal event first, then `finish`, B5). Also keeps `awaiting_input`
    /// in step: a question sets it; any other event after a question means
    /// the agent moved on, so the call is followed again.
    pub async fn on_event(
        &mut self,
        kind: AgentEventKind,
        remote_event_id: Option<String>,
        payload: Payload,
    ) -> Flow {
        let end = self.ports.rules.end_of(&kind, &payload);
        let question = matches!(
            kind,
            AgentEventKind::InputRequired { .. } | AgentEventKind::AuthRequired { .. }
        );
        let admitted = self.report(kind, remote_event_id, payload).await?;
        if question {
            self.awaiting_input = true;
        } else if self.awaiting_input {
            // The agent moved on without a reply through this Connector.
            self.awaiting_input = false;
            self.waiting_ticks = 0;
            self.reattach_due = self.events.is_none();
        }
        if let (Some(outcome), Admitted::Yes) = (end, admitted) {
            let evidence = match outcome {
                FinalDispatchOutcome::Completed => "final_admitted",
                FinalDispatchOutcome::Failed => "error_admitted",
                FinalDispatchOutcome::Canceled => "canceled_admitted",
            };
            self.finish(outcome, evidence).await?;
        }
        Ok(())
    }

    /// Normalizes one event with the rules and writes it to the inbox.
    /// Returns only once the write landed; a lost holder stops the actor and
    /// a permanent store error parks it (`Stop::Invalid`).
    pub async fn report(
        &mut self,
        kind: AgentEventKind,
        remote_event_id: Option<String>,
        payload: Payload,
    ) -> Flow<Admitted> {
        // Identity and digest come from the event as the agent sent it; only
        // afterwards a large payload is moved to blob storage.
        let report = self
            .ports
            .rules
            .report(self.held.view(), &mut self.cursor, kind, remote_event_id, payload);
        match report {
            Report::Stream(mut append) => {
                append.event.payload = self.offload(append.event.payload).await?;
                self.append(append).await?;
                Ok(Admitted::Yes)
            }
            Report::Key(mut request) => {
                if let InputBody::Agent { event } = &mut request.input.body {
                    let payload = std::mem::replace(&mut event.payload, Payload::Inline(serde_json::Value::Null));
                    event.payload = self.offload(payload).await?;
                }
                self.admit(request).await
            }
        }
    }

    /// Ends the dispatch in `disp` (phase `ended`) with `outcome` and an
    /// evidence code saying why, then stops following the call. Called only
    /// after the terminal event was admitted, or after its session turned out
    /// to be quarantined (the event was then not delivered, so an ended call
    /// does not always have its terminal event in the inbox). Finishing twice
    /// with the same outcome is harmless; another outcome already stored is
    /// only logged.
    pub async fn finish(&mut self, outcome: FinalDispatchOutcome, evidence: &str) -> Flow {
        let written = held_write!(self, "finish", self.held.finish(outcome, evidence.to_owned()));
        if written == IdempotentWrite::ContentConflict {
            tracing::error!(did = %self.did().0, ?outcome, "the call already ended with another outcome");
        }
        self.ended = true;
        self.events = None;
        Ok(())
    }

    /// Admits a key event, retrying a temporary error with the same input id
    /// until it lands. A content conflict marks the call `conflicted`.
    async fn admit(&mut self, request: ConnectorAdmission) -> Flow<Admitted> {
        let mut backoff = self.backoff();
        loop {
            match self.ports.inbox.admit_connector(request.clone()).await {
                Ok(AdmitOutcome::ContentConflict) => {
                    tracing::error!(
                        did = %self.did().0, input_id = %request.input.input_id.0,
                        "key event conflicts with one admitted earlier; the call is not ended on it"
                    );
                    self.conflicted = true;
                    self.status.pending_effect.store(true, std::sync::atomic::Ordering::SeqCst);
                    return Ok(Admitted::Conflict);
                }
                // The session is quarantined: retrying cannot help, and the
                // call is still allowed to end.
                Ok(AdmitOutcome::Quarantined) => {
                    tracing::warn!(did = %self.did().0, "session quarantined; key event not delivered");
                    return Ok(Admitted::Yes);
                }
                // Every other answer is success: `Accepted` and `Duplicate`
                // (admitted earlier) are deliberately not told apart, because
                // a re-report by a later holder is the same success as the
                // first one (B11).
                Ok(_) => return Ok(Admitted::Yes),
                Err(error) if crate::call::permanent(&error) => {
                    tracing::error!(did = %self.did().0, ?error, "admission cannot succeed; the call stays held");
                    return Err(Stop::Invalid);
                }
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "admission failed; retrying with the same input id");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// Appends an intermediate event, retrying a temporary error until it
    /// lands.
    async fn append(&mut self, request: AppendAgentEvent) -> Flow {
        let mut backoff = self.backoff();
        loop {
            // Same (did, holder_epoch, seq) on every attempt: the Worker drops repeats.
            match self.ports.inbox.append_stream(request.clone()).await {
                Ok(_) => return Ok(()),
                Err(error) if crate::call::permanent(&error) => {
                    tracing::error!(did = %self.did().0, ?error, "event append cannot succeed; the call stays held");
                    return Err(Stop::Invalid);
                }
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "event append failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// Between retries: stop when another holder took the call. Any other
    /// read error is ignored here; the retry loop goes on.
    async fn still_holder(&mut self) -> Flow {
        match self.held.refresh().await {
            Err(HolderError::Lost) => Err(Stop::Lost),
            _ => Ok(()),
        }
    }

    /// Moves a payload above the inline limit to blob storage.
    ///
    /// ```text
    /// key:    bcn/agent-event/<did>/<sha256 hex of the JSON bytes>
    /// digest: bcs_dist_encoding::blob_reference digest of the same bytes
    /// value:  the JSON bytes; the event then carries Payload::Ref(reference)
    /// ```
    /// Writing the same content twice is `AlreadyApplied`, so retries and a
    /// later holder's re-report are harmless.
    async fn offload(&mut self, payload: Payload) -> Flow<Payload> {
        let Payload::Inline(value) = &payload else {
            return Ok(payload);
        };
        let Ok(bytes) = serde_json::to_vec(value) else {
            return Ok(payload);
        };
        if bytes.len() <= self.config.max_inline_bytes {
            return Ok(payload);
        }
        let hash = hex::encode(Sha256::digest(&bytes));
        let reference = bcs_dist_encoding::blob_reference(
            &format!("bcn/agent-event/{}/{hash}", self.did().0),
            &bytes,
        );
        let mut backoff = self.backoff();
        loop {
            match self.ports.blobs.put_immutable(reference.clone(), bytes.clone()).await {
                Ok(IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied) => {
                    return Ok(Payload::Ref(reference));
                }
                Ok(IdempotentWrite::ContentConflict) => {
                    // The key is the content hash, so this is a store fault;
                    // keep the content inline rather than lose it.
                    tracing::error!(did = %self.did().0, "blob identity conflict; payload kept inline");
                    return Ok(payload);
                }
                Err(error) if crate::call::permanent(&error) => {
                    tracing::error!(did = %self.did().0, ?error, "blob write cannot succeed; the call stays held");
                    return Err(Stop::Invalid);
                }
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "blob write failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// Loads a payload the Worker stored by reference (a request or a
    /// reply). `None` = it is gone, or its bytes are not valid JSON.
    pub async fn load(&mut self, payload: &Payload) -> Flow<Option<Payload>> {
        let Payload::Ref(reference) = payload else {
            return Ok(Some(payload.clone()));
        };
        let mut backoff = self.backoff();
        loop {
            match self.ports.blobs.get(reference).await {
                Ok(Some(bytes)) => {
                    return Ok(serde_json::from_slice(&bytes).ok().map(Payload::Inline));
                }
                Ok(None) => return Ok(None),
                Err(error) if crate::call::permanent(&error) => {
                    tracing::error!(did = %self.did().0, ?error, "blob read cannot succeed; the call stays held");
                    return Err(Stop::Invalid);
                }
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "blob read failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// A call that ends without the agent's own terminal event: report an
    /// error event `{"state": <state>, "code": <code>}` and finish once it is
    /// admitted. The `state` word decides the outcome the rules see.
    pub async fn end_with_error(
        &mut self,
        state: &str,
        code: &str,
        outcome: FinalDispatchOutcome,
        evidence: &str,
    ) -> Flow {
        let payload = Payload::Inline(serde_json::json!({ "state": state, "code": code }));
        if self.report(AgentEventKind::Error, None, payload).await? == Admitted::Yes {
            self.finish(outcome, evidence).await?;
        }
        Ok(())
    }

    /// A cancel arrived before anything was sent: nothing to tell the agent.
    pub async fn end_canceled_unsent(&mut self) -> Flow {
        self.end_with_error(
            "canceled",
            "canceled_before_send",
            FinalDispatchOutcome::Canceled,
            "canceled_before_send",
        )
        .await
    }
}
