//! The `HeldDispatchPort` provider: the holder's writes to its dispatch.
//!
//! Every write is: read, check that the holder is still this handle's
//! `{instance, epoch}`, then write at `read version + 1`. A rejected write
//! re-reads and decides again from the fresh document (the Worker may have
//! written `control` in between); it never retries with a bigger number.

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

pub struct StoreHeldDispatch {
    shared: Arc<Shared>,
    holder: Holder,
    view: DispatchView,
    version: Version,
    /// Highest input seq this handle itself recorded send intent for.
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

    async fn begin_send(&mut self, operation: SendOperation) -> Result<SendPermit, HolderError> {
        let permit = SendPermit {
            did: self.view.spec.did.clone(),
            holder: self.holder.clone(),
            operation: operation.clone(),
        };
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
            // Nobody recorded intent before this read, and only this holder
            // can write it from here on: whatever is recorded later is this
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
                    if doc.connector.input_sending.is_some_and(|sent| sent >= *seq) {
                        return Ok(Step::Keep(permit.clone()));
                    }
                    let mut connector = doc.connector.clone();
                    connector.input_sending = Some(*seq);
                    Ok(Step::Write(Box::new(execution.clone()), connector, permit.clone()))
                }
            }
        })
        .await?;
        Ok(permit)
    }

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
