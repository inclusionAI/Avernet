//! Reporting agent events to the session inbox.
//!
//! Key events are admitted under their stable identity; intermediate events
//! are appended. Both are retried with the same identity until they land or
//! the holder is lost. A transport problem is never turned into an agent event.

use crate::call::{Call, Flow, Stop, held_write};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::Report;
use bcs_service_api::port::repo::distributed::common::IdempotentWrite;
use bcs_service_api::port::repo::distributed::dispatch::HolderError;
use bcs_service_api::port::repo::distributed::inbox::{AdmitOutcome, AdmitRequest, AppendAgentEvent};
use sha2::{Digest as _, Sha256};

/// How an admission ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Admitted {
    Yes,
    /// Another content was admitted under the same identity. Cannot happen
    /// with the payload-free digest; treated as a bug, the call is not ended.
    Conflict,
}

impl Call {
    /// Reports one remote event and, when it is terminal, finishes the call
    /// (terminal event first, then `finish`).
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

    pub async fn finish(&mut self, outcome: FinalDispatchOutcome, evidence: &str) -> Flow {
        let written = held_write!(self, "finish", self.held.finish(outcome, evidence.to_owned()));
        if written == IdempotentWrite::ContentConflict {
            tracing::error!(did = %self.did().0, ?outcome, "the call already ended with another outcome");
        }
        self.ended = true;
        self.events = None;
        Ok(())
    }

    async fn admit(&mut self, request: AdmitRequest) -> Flow<Admitted> {
        let mut backoff = self.backoff();
        loop {
            match self.ports.inbox.admit(request.clone()).await {
                Ok(AdmitOutcome::ContentConflict) => {
                    tracing::error!(
                        did = %self.did().0, input_id = %request.input.input_id.0,
                        "key event conflicts with one admitted earlier; the call is not ended on it"
                    );
                    return Ok(Admitted::Conflict);
                }
                Ok(AdmitOutcome::Quarantined) => {
                    tracing::warn!(did = %self.did().0, "session quarantined; key event not delivered");
                    return Ok(Admitted::Yes);
                }
                // Every other answer is success. The answer for an input that
                // was admitted earlier is deliberately not named: the merged
                // contract drops that variant (decision 24), and a re-report
                // by a later holder is the same success as the first one.
                Ok(_) => return Ok(Admitted::Yes),
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "admission failed; retrying with the same input id");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    async fn append(&mut self, request: AppendAgentEvent) -> Flow {
        let mut backoff = self.backoff();
        loop {
            // Same (did, holder_epoch, seq) on every attempt: the Worker drops repeats.
            match self.ports.inbox.append_stream(request.clone()).await {
                Ok(_) => return Ok(()),
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "event append failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// Between retries: stop when another holder took the call.
    async fn still_holder(&mut self) -> Flow {
        match self.held.refresh().await {
            Err(HolderError::Lost) => Err(Stop::Lost),
            _ => Ok(()),
        }
    }

    /// Moves a payload above the inline limit to blob storage.
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
        let reference = BlobRef {
            key: format!("bcn/agent-event/{}/{hash}", self.did().0),
            digest: Digest(format!("sha256:{hash}")),
        };
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
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "blob write failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// Loads a payload the Worker stored by reference. `None` = it is gone.
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
                Err(error) => {
                    tracing::warn!(did = %self.did().0, ?error, "blob read failed; retrying");
                    backoff.wait().await;
                    self.still_holder().await?;
                }
            }
        }
    }

    /// A call that ends without the agent's own terminal event.
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
