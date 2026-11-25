//! The `HeldDispatchPort` provider: the holder's writes to its dispatch.
//!
//! Every write is: read, check that the holder is still this handle's
//! `{instance, epoch}`, then write at `read version + 1`. A rejected write
//! re-reads and decides again from the fresh document (the Worker may have
//! written `control` in between); it never retries with a bigger number.
//! A bigger number would apply on top of a write this holder has not seen,
//! so the decision would rest on a stale read.
//!
//! Where this sits: a claim (`queue.rs`) returns a [`StoreHeldDispatch`];
//! the Connector's actor for that call keeps it for the call's life and
//! makes every store change through it. When the handle answers
//! `HolderError::Lost`, another generation holds the call and the actor stops.
//!
//! Keys touched:
//!
//! ```text
//! disp:       bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1
//!             patch e.g. {"execution": {"phase": "accepted",
//!             "remote": {"task_id": "t-81", "context_id": null}}}
//!             written at read version + 1, e.g. 2000000004 -> 2000000005
//! held index: bcn:v1:held:c01:main:conn-03:1794370950
//!             member "s0042.7f3a9c.b21e.n2.a1" removed by `finish`
//! ```
//!
//! Glossary:
//! - **Transition**: one change of the document from one state to the next
//!   (e.g. `claimed -> sending`), decided from a fresh fenced read.
//! - **Send intent**: the durable record, written before any network call,
//!   that a message may be on its way to the agent (spec rule B3). For the
//!   first message it is the phase `sending`; for a human reply it is
//!   `/connector/input_sending`.
//! - **Settled**: the answer to give when an earlier attempt's write had an
//!   unknown outcome and the re-read shows it did land.

use crate::doc::{ConnectorState, DispatchDoc, holder_view};
use crate::held_index;
use crate::shared::{Shared, Wrote};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::version;
use bcs_service_api::core::dist_connector::INPUT_MAYBE_SENT;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::dispatch::{
    AcceptanceEvidence, HeldDispatchPort, HolderError, SendOperation, SendPermit,
};
use bcs_store_api::Version;
use std::sync::Arc;

/// The actor's handle on one claimed call.
///
/// - Proves ownership: only a claim (or resume) creates one, for the holder
///   `{instance, epoch}` it wrote.
/// - Fences: every read checks the stored holder is still this one, and
///   every write is conditional on the version just read (B18).
/// - Offers the state transitions the actor may make: start a send, record
///   the agent's acceptance, confirm a reply, record a cancel observation,
///   finish.
///
/// It makes no network call and no decision about the agent; it refuses a
/// transition the stored state does not allow (`InvalidTransition`).
pub struct StoreHeldDispatch {
    shared: Arc<Shared>,
    /// The `{instance, epoch}` this handle claimed as.
    holder: Holder,
    /// The last document read or written, as the holder sees it (confirmed
    /// replies hidden, see `doc::holder_view`).
    view: DispatchView,
    /// The store version of `view`.
    version: Version,
    /// Highest input seq this handle itself recorded send intent for. Kept in
    /// memory only: it tells this handle's own intent apart from a former
    /// generation's in `begin_send`.
    own_input_intent: Option<InputSeq>,
}

/// What a transition decided from one read of the document.
enum Step<T> {
    /// The document already shows the wanted state.
    Keep(T),
    /// Write these two objects, then answer `T`.
    Write(Box<DispatchExecution>, ConnectorState, T),
}

fn invalid<T>(code: &str) -> Result<T, HolderError> {
    Err(HolderError::InvalidTransition {
        code: code.to_owned(),
    })
}

/// Rejects a remote task reference that is empty or too large to store
/// (task id and context id at most 1024 bytes, whole JSON at most 4096).
fn validate_remote(remote: &RemoteTaskRef) -> Result<(), HolderError> {
    if remote.task_id.is_empty()
        || remote.task_id.len() > 1024
        || remote.context_id.as_ref().is_some_and(|s| s.len() > 1024)
        || serde_json::to_vec(remote).map_or(true, |b| b.len() > 4096)
    {
        return invalid("remote_identity");
    }
    Ok(())
}

impl StoreHeldDispatch {
    pub(crate) fn new(shared: Arc<Shared>, holder: Holder, view: DispatchView, version: Version) -> Self {
        Self {
            shared,
            holder,
            view: holder_view(view),
            version,
            own_input_intent: None,
        }
    }

    /// The version this handle last read or wrote.
    pub fn version(&self) -> Version {
        self.version
    }

    /// Reads the document and fences: a missing document or another holder
    /// (or another generation of this instance) means the call is lost.
    async fn read_fenced(&mut self) -> Result<DispatchDoc, HolderError> {
        let did = self.view.spec.did.clone();
        let doc = self.shared.read(&did).await?.ok_or(HolderError::Lost)?;
        if doc.view.execution.holder.as_ref() != Some(&self.holder) {
            return Err(HolderError::Lost);
        }
        self.version = doc.version;
        self.view = holder_view(doc.view.clone());
        Ok(doc)
    }

    /// Runs one transition. `settled` is the answer when a write of unknown
    /// outcome turns out to have landed (the re-read then asks for no write).
    ///
    /// Each attempt: fenced read, then `decide` on that read, then either
    /// answer (`Keep`) or write at `read version + 1` (`Write`). `decide` is
    /// called again on every fresh read, so it must only look at the document
    /// it is given. Up to `write_attempts` attempts; then `NotApplied`
    /// (`disp_write_contended`), or `OutcomeUnknown` if any attempt's write
    /// had an unknown outcome. A `?` on the read or on `decide` ends the
    /// transition with that error (`Lost` when the holder changed).
    ///
    /// Example of `settled`: `confirm_input` writes, the answer is lost
    /// (`Unknown`), the re-read shows the input confirmed, so `decide` says
    /// `Keep(AlreadyApplied)`; but the write was this handle's own, so the
    /// answer is `settled` = `Applied`. `None` keeps `decide`'s answer.
    async fn transition<T: Clone>(
        &mut self,
        settled: Option<T>,
        decide: impl Fn(&DispatchDoc) -> Result<Step<T>, HolderError>,
    ) -> Result<T, HolderError> {
        let did = self.view.spec.did.clone();
        let mut unknown = false;
        for _ in 0..self.shared.config.write_attempts {
            let doc = match self.read_fenced().await {
                // An earlier attempt may have landed: a failed read cannot
                // turn that into "definitely not applied".
                Err(HolderError::Store(_)) if unknown => {
                    return Err(HolderError::Store(StoreError::OutcomeUnknown {
                        operation_id: did.0,
                    }));
                }
                other => other?,
            };
            let (execution, connector, answer) = match decide(&doc)? {
                Step::Keep(answer) => {
                    return Ok(match (&settled, unknown) {
                        (Some(settled), true) => settled.clone(),
                        _ => answer,
                    });
                }
                Step::Write(execution, connector, answer) => (execution, connector, answer),
            };
            let next = version::next(doc.version)?;
            match self.shared.write(&did, next, &doc, &execution, &connector).await? {
                Wrote::Applied(version) => {
                    self.version = version;
                    let mut view = doc.view;
                    view.execution = *execution;
                    self.view = holder_view(view);
                    return Ok(answer);
                }
                // Someone wrote since the read: decide again from a fresh read.
                Wrote::Rejected => {}
                Wrote::Unknown => unknown = true,
            }
        }
        Err(HolderError::Store(if unknown {
            StoreError::OutcomeUnknown {
                operation_id: did.0,
            }
        } else {
            StoreError::NotApplied {
                code: "disp_write_contended".into(),
            }
        }))
    }
}

#[async_trait]
impl HeldDispatchPort for StoreHeldDispatch {
    fn view(&self) -> &DispatchView {
        &self.view
    }

    fn holder(&self) -> &Holder {
        &self.holder
    }

    async fn refresh(&mut self) -> Result<DispatchView, HolderError> {
        self.read_fenced().await?;
        Ok(self.view.clone())
    }

    /// Records send intent — with the durable `SendProof` of exactly what
    /// this attempt is (plan T6) — before any network call to the agent (B3),
    /// and returns the permit to send. The proof must say the same request
    /// id (and input seq) as the operation it is written with, or the write
    /// is refused (`send_proof_mismatch`).
    ///
    /// - `Start` (the first message): `claimed -> sending`. Finding `sending`
    ///   already gives the permit without a write (intent and proof are
    ///   durable). A new holder that finds `sending` after its claim must
    ///   first resolve the start (B15); that decision is the actor's, not
    ///   this handle's.
    /// - `Input { seq }` (a human reply, B12): allowed only in `accepted`,
    ///   for `seq = confirmed + 1`, while the Worker still has `seq` queued
    ///   in `/control/pending_input`. Writes `/connector/input_sending = seq`.
    ///
    /// The pre-check for `Input` refuses with `INPUT_MAYBE_SENT` when a former
    /// generation recorded intent for this seq (`recorded && !mine`) and the
    /// seq is not yet confirmed. `!confirmed` keeps a false "maybe sent"
    /// away from an input the agent definitely accepted: such a request is
    /// refused as `input_out_of_order` by the transition instead.
    async fn begin_send(
        &mut self,
        operation: SendOperation,
        proof: SendProof,
    ) -> Result<SendPermit, HolderError> {
        let permit = SendPermit {
            did: self.view.spec.did.clone(),
            holder: self.holder.clone(),
            operation: operation.clone(),
        };
        // The proof is written with the intent it belongs to: request id
        // (and input seq) must say the same thing as the operation.
        match (&operation, &proof.operation) {
            (
                SendOperation::Start { request_id },
                ProofOperation::Start { request_id: proved },
            ) if request_id == proved => {}
            (
                SendOperation::Input { seq, request_id },
                ProofOperation::Input { seq: proved_seq, request_id: proved },
            ) if seq == proved_seq && request_id == proved => {}
            _ => return invalid("send_proof_mismatch"),
        }
        if let SendOperation::Input { seq, .. } = &operation {
            // Intent recorded by a former holder generation: the input may
            // already have reached the agent, so no fresh permit is given.
            let doc = self.read_fenced().await?;
            let recorded = doc.connector.input_sending.is_some_and(|sent| sent >= *seq);
            let mine = self.own_input_intent.is_some_and(|sent| sent >= *seq);
            let confirmed = doc.view.execution.confirmed_input_through.is_some_and(|c| c >= *seq);
            if recorded && !mine && !confirmed {
                return invalid(INPUT_MAYBE_SENT);
            }
            // Past the check, intent for `seq` was either not recorded at
            // this read, or recorded by this handle, or `seq` is already
            // confirmed (the transition refuses that). Only this holder can
            // write it from here on, so whatever is recorded later is this
            // handle's own, even if the write's answer is lost.
            self.own_input_intent = self.own_input_intent.max(Some(*seq));
        }
        let permit = self.transition(None, |doc| {
            let execution = &doc.view.execution;
            match &operation {
                SendOperation::Start { .. } => match execution.phase {
                    DispatchPhase::Claimed => {
                        let mut next = execution.clone();
                        next.phase = DispatchPhase::Sending;
                        // The proof of the first attempt is durable with the
                        // intent, before any network call (plan T6).
                        next.send_proof = Some(proof.clone());
                        Ok(Step::Write(Box::new(next), doc.connector.clone(), permit.clone()))
                    }
                    // Intent is already durable: a fenced read is enough.
                    DispatchPhase::Sending => Ok(Step::Keep(permit.clone())),
                    _ => invalid("start_not_allowed_in_phase"),
                },
                SendOperation::Input { seq, .. } => {
                    if execution.phase != DispatchPhase::Accepted {
                        return invalid("input_not_allowed_in_phase");
                    }
                    let confirmed = execution.confirmed_input_through.map_or(0, |s| s.0);
                    if seq.0 != confirmed.saturating_add(1) {
                        return invalid("input_out_of_order");
                    }
                    if !doc.view.control.pending_input.iter().any(|p| p.seq == *seq) {
                        return invalid("input_not_pending");
                    }
                    // Intent already recorded, and the pre-check showed it is
                    // this handle's own (e.g. a resend after an unknown
                    // write): idempotent, nothing to write.
                    if doc.connector.input_sending.is_some_and(|sent| sent >= *seq) {
                        return Ok(Step::Keep(permit.clone()));
                    }
                    let mut connector = doc.connector.clone();
                    connector.input_sending = Some(*seq);
                    let mut next = execution.clone();
                    next.send_proof = Some(proof.clone());
                    Ok(Step::Write(Box::new(next), connector, permit.clone()))
                }
            }
        })
        .await?;
        Ok(permit)
    }

    /// Records that the agent accepted the first message and stores its task
    /// reference: `sending -> accepted` with `/execution/remote` (B4, before
    /// any event is reported). Repeating it with the same task id is kept;
    /// another task id is `remote_task_mismatch`.
    ///
    /// Returns the Worker's cancel intent (`/control/cancel`) from the same
    /// read the write was based on. A cancel written while the start was in
    /// flight could not be carried out (there was no remote task yet); with
    /// it in the answer the actor can cancel the new remote task at once.
    async fn record_acceptance(
        &mut self,
        evidence: AcceptanceEvidence,
    ) -> Result<Option<CancelIntent>, HolderError> {
        validate_remote(&evidence.remote)?;
        if !matches!(evidence.operation, SendOperation::Start { .. }) {
            return invalid("acceptance_of_input");
        }
        self.transition(None, |doc| {
            let execution = &doc.view.execution;
            // The cancel intent comes from the same read the write is based on.
            let cancel = doc.view.control.cancel.clone();
            match execution.phase {
                DispatchPhase::Sending => {
                    let mut next = execution.clone();
                    next.phase = DispatchPhase::Accepted;
                    next.remote = Some(evidence.remote.clone());
                    Ok(Step::Write(Box::new(next), doc.connector.clone(), cancel))
                }
                DispatchPhase::Accepted
                    if execution.remote.as_ref().map(|r| &r.task_id)
                        == Some(&evidence.remote.task_id) =>
                {
                    Ok(Step::Keep(cancel))
                }
                DispatchPhase::Accepted => invalid("remote_task_mismatch"),
                _ => invalid("acceptance_not_allowed_in_phase"),
            }
        })
        .await
    }

    /// Records that the agent definitely accepted reply `seq`: sets
    /// `/execution/confirmed_input_through = seq` (B12). Only as a gap-free
    /// prefix (`seq = confirmed + 1`), only for a reply the Worker queued and
    /// this holder recorded send intent for, and only on the stored remote
    /// task. An already confirmed seq answers `AlreadyApplied`.
    ///
    /// The entry stays in `/control/pending_input` until the Worker removes
    /// it; the holder's view hides it from now on.
    async fn confirm_input(
        &mut self,
        seq: InputSeq,
        evidence: AcceptanceEvidence,
    ) -> Result<IdempotentWrite, HolderError> {
        validate_remote(&evidence.remote)?;
        if !matches!(&evidence.operation, SendOperation::Input { seq: sent, .. } if *sent == seq) {
            return invalid("confirm_evidence_mismatch");
        }
        self.transition(Some(IdempotentWrite::Applied), |doc| {
            let execution = &doc.view.execution;
            let confirmed = execution.confirmed_input_through.map_or(0, |s| s.0);
            if seq.0 <= confirmed {
                return Ok(Step::Keep(IdempotentWrite::AlreadyApplied));
            }
            // Only a gap-free prefix: an unknown earlier input is never skipped.
            if seq.0 != confirmed.saturating_add(1) {
                return invalid("input_gap");
            }
            if execution.phase != DispatchPhase::Accepted {
                return invalid("confirm_not_allowed_in_phase");
            }
            // Confirming an input the Worker never queued would hide it later.
            if !doc.view.control.pending_input.iter().any(|p| p.seq == seq) {
                return invalid("input_not_pending");
            }
            if doc.connector.input_sending.is_none_or(|sent| sent < seq) { return invalid("send_intent_missing"); }
            if execution.remote.as_ref() != Some(&evidence.remote) { return invalid("remote_task_mismatch"); }
            let mut next = execution.clone();
            next.confirmed_input_through = Some(seq);
            Ok(Step::Write(Box::new(next), doc.connector.clone(), IdempotentWrite::Applied))
        })
        .await
    }

    /// Stores what the holder knows about carrying out the Worker's cancel
    /// (`/execution/cancellation`, B13). `RequestAccepted` ("the agent took
    /// the cancel request") and `ConfirmedCanceled` ("the task is canceled")
    /// stay distinct. A definite result is never replaced and nothing goes
    /// back to `not_sent` (`cancel_observation_backward`); the same value
    /// again answers `AlreadyApplied`.
    async fn record_cancel(
        &mut self,
        observation: CancelObservation,
    ) -> Result<IdempotentWrite, HolderError> {
        self.transition(Some(IdempotentWrite::Applied), |doc| {
            let execution = &doc.view.execution;
            let current = &execution.cancellation;
            if *current == observation {
                return Ok(Step::Keep(IdempotentWrite::AlreadyApplied));
            }
            // A definite result is never replaced, and nothing goes back to
            // "not sent". An accepted request only moves on to a definite
            // result. An unknown or refused attempt may be tried again.
            let settled = matches!(
                current,
                CancelObservation::ConfirmedCanceled | CancelObservation::AlreadyFinished { .. }
            ) || (*current == CancelObservation::RequestAccepted
                && !matches!(
                    observation,
                    CancelObservation::ConfirmedCanceled | CancelObservation::AlreadyFinished { .. }
                ));
            if settled || observation == CancelObservation::NotSent {
                return invalid("cancel_observation_backward");
            }
            let mut next = execution.clone();
            next.cancellation = observation.clone();
            Ok(Step::Write(Box::new(next), doc.connector.clone(), IdempotentWrite::Applied))
        })
        .await
    }

    /// Ends the call: phase `ended` with its outcome, and the evidence string
    /// in `/connector/finish_evidence` (non-empty, at most 4096 bytes as JSON).
    /// Called after the final event was admitted (B5). Ending it again with
    /// the same outcome answers `AlreadyApplied`; with another outcome
    /// `ContentConflict` (nothing is written). Then the call is removed from
    /// the held index (B6); a failed removal is only logged.
    async fn finish(
        &mut self,
        outcome: FinalDispatchOutcome,
        evidence: String,
    ) -> Result<IdempotentWrite, HolderError> {
        if evidence.is_empty() || serde_json::to_vec(&evidence).map_or(true, |b| b.len() > 4096) {
            return invalid("terminal_evidence");
        }
        let stored = match outcome {
            FinalDispatchOutcome::Completed => DispatchOutcome::Completed,
            FinalDispatchOutcome::Failed => DispatchOutcome::Failed,
            FinalDispatchOutcome::Canceled => DispatchOutcome::Canceled,
        };
        let written = self
            .transition(Some(IdempotentWrite::Applied), |doc| {
                let execution = &doc.view.execution;
                if execution.phase == DispatchPhase::Ended {
                    return Ok(Step::Keep(if execution.outcome == Some(stored) {
                        IdempotentWrite::AlreadyApplied
                    } else {
                        IdempotentWrite::ContentConflict
                    }));
                }
                let mut next = execution.clone();
                next.phase = DispatchPhase::Ended;
                next.outcome = Some(stored);
                let mut connector = doc.connector.clone();
                connector.finish_evidence = Some(evidence.clone());
                Ok(Step::Write(Box::new(next), connector, IdempotentWrite::Applied))
            })
            .await?;
        // Unlisted only after ENDED is durable. A failure leaves the index
        // listing a finished call, which is harmless.
        let did = self.view.spec.did.clone();
        if let Err(error) = held_index::remove(&self.shared, &self.holder.instance, &did).await {
            tracing::warn!(?error, did = %did.0, "held index removal failed after finish");
        }
        Ok(written)
    }
}
