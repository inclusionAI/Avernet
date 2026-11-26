//! The restricted holder of a handed-off dispatch (plan T7).
//!
//! Every write is fenced by the **independent verification claim epoch** in
//! the dispatch's handoff record — not by the holder line — so a verifier
//! completes the handoff even while the original Connector is still alive,
//! and a write from an epoch that is no longer current is refused. Only the
//! operations the handoff approved are legal: binding the task a query
//! found by its stable key, carrying out an approved cancel, persistent
//! reports through the ordinary inbox path, quarantining at the deadline,
//! and finishing on verified stop evidence. Start, resend, continuation and
//! input confirms are refused in this mode.

use crate::doc::{self, ConnectorState, DispatchDoc, holder_view};
use crate::shared::{Shared, Wrote};
use crate::verify_index;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::version;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::dispatch::{
    AcceptanceEvidence, HeldDispatchPort, HolderError, SendOperation, SendPermit,
    VerificationHandoffDraft,
};
use bcs_store_api::Version;
use std::sync::Arc;

/// A verifier's handle over one dispatch, claimed at `claim`.
pub struct StoreVerificationDispatch {
    shared: Arc<Shared>,
    claim: VerificationClaim,
    /// The reporting generation of this handle: the claim's identity, so
    /// every persisted record names the verifier that wrote it.
    holder: Holder,
    view: DispatchView,
    version: Version,
}

/// What a transition decided from one fenced read: either nothing to write,
/// or the full execution plus an optional `due` obligation to rewrite.
enum Step<T> {
    Keep(T),
    Write {
        execution: Box<DispatchExecution>,
        connector: ConnectorState,
        due: Option<TimestampMillis>,
        answer: T,
    },
}

fn invalid<T>(code: &str) -> Result<T, HolderError> {
    Err(HolderError::InvalidTransition { code: code.to_owned() })
}

const QUARANTINE_REASON_MAX: usize = 128;

impl StoreVerificationDispatch {
    pub(crate) fn new(
        shared: Arc<Shared>,
        claim: VerificationClaim,
        view: DispatchView,
        version: Version,
    ) -> Self {
        Self {
            holder: Holder {
                instance: claim.instance.clone(),
                epoch: claim.epoch,
            },
            shared,
            claim,
            view: holder_view(view),
            version,
        }
    }

    /// Reads the document and fences by the verification claim: a missing
    /// document, a dispatch no longer in verification mode, or a claim that
    /// a later verifier superseded all mean the handle is lost.
    async fn read_fenced(&mut self) -> Result<DispatchDoc, HolderError> {
        let did = self.view.spec.did.clone();
        let doc = self.shared.read(&did).await?.ok_or(HolderError::Lost)?;
        let Some(handoff) = &doc.view.execution.verification else {
            return Err(HolderError::Lost);
        };
        if handoff.claim.as_ref() != Some(&self.claim) {
            return Err(HolderError::Lost);
        }
        self.version = doc.version;
        self.view = holder_view(doc.view.clone());
        Ok(doc)
    }

    /// One transition: read fenced, decide, write the execution (and the
    /// `due` obligation when decided) at the read version + 1. Unknown write
    /// outcomes resolve by re-reading, never by guessing.
    async fn transition<T: Clone>(
        &mut self,
        settled: Option<T>,
        decide: impl Fn(&DispatchDoc) -> Result<Step<T>, HolderError>,
    ) -> Result<T, HolderError> {
        let did = self.view.spec.did.clone();
        let mut unknown = false;
        for _ in 0..self.shared.config.write_attempts {
            let doc = match self.read_fenced().await {
                Err(HolderError::Store(_)) if unknown => {
                    return Err(HolderError::Store(StoreError::OutcomeUnknown {
                        operation_id: did.0,
                    }));
                }
                other => other?,
            };
            let (execution, connector, due, answer) = match decide(&doc)? {
                Step::Keep(answer) => {
                    return Ok(match (&settled, unknown) {
                        (Some(settled), true) => settled.clone(),
                        _ => answer,
                    });
                }
                Step::Write { execution, connector, due, answer } => {
                    (execution, connector, due, answer)
                }
            };
            let next = version::next(doc.version)?;
            let mutation = match due {
                Some(at) => doc::connector_write_due(&doc, &execution, &connector, at)?,
                None => doc::connector_write(&doc, &execution, &connector)?,
            };
            match self.shared.mutate(&did, next, mutation).await? {
                Wrote::Applied(version) => {
                    self.version = version;
                    let mut view = doc.view;
                    view.execution = *execution;
                    self.view = holder_view(view);
                    return Ok(answer);
                }
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
impl HeldDispatchPort for StoreVerificationDispatch {
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

    /// Start, resend and continuation are refused in verification mode.
    async fn begin_send(
        &mut self,
        _operation: SendOperation,
        _proof: SendProof,
    ) -> Result<SendPermit, HolderError> {
        Err(HolderError::InvalidTransition {
            code: "verification_mode".into(),
        })
    }

    /// A query by the stable key found the original task: bind it, exactly
    /// like a start acceptance, but through the verification claim fence.
    async fn record_acceptance(
        &mut self,
        evidence: AcceptanceEvidence,
    ) -> Result<Option<CancelIntent>, HolderError> {
        if evidence.remote.task_id.is_empty()
            || evidence.remote.task_id.len() > 1024
            || evidence.remote.context_id.as_ref().is_some_and(|s| s.len() > 1024)
            || serde_json::to_vec(&evidence.remote).map_or(true, |b| b.len() > 4096)
        {
            return invalid("remote_identity");
        }
        if !matches!(evidence.operation, SendOperation::Start { .. }) {
            return invalid("acceptance_of_input");
        }
        self.transition(None, |doc| {
            let execution = &doc.view.execution;
            let cancel = doc.view.control.cancel.clone();
            match execution.phase {
                DispatchPhase::Sending => {
                    let mut next = execution.clone();
                    next.phase = DispatchPhase::Accepted;
                    next.remote = Some(evidence.remote.clone());
                    Ok(Step::Write {
                        execution: Box::new(next),
                        connector: doc.connector.clone(),
                        due: None,
                        answer: cancel,
                    })
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

    /// Inputs are not confirmed by a verifier: the verification lane never
    /// continues the remote task.
    async fn confirm_input(
        &mut self,
        _seq: InputSeq,
        _evidence: AcceptanceEvidence,
    ) -> Result<IdempotentWrite, HolderError> {
        Err(HolderError::InvalidTransition {
            code: "verification_mode".into(),
        })
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
            Ok(Step::Write {
                execution: Box::new(next),
                connector: doc.connector.clone(),
                due: None,
                answer: IdempotentWrite::Applied,
            })
        })
        .await
    }

    /// Finishing on verified stop evidence completes the handoff; the entry
    /// leaves the verification queue index only after ENDED is durable.
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
                Ok(Step::Write {
                    execution: Box::new(next),
                    connector,
                    due: None,
                    answer: IdempotentWrite::Applied,
                })
            })
            .await?;
        let did = self.view.spec.did.clone();
        if let Err(error) = verify_index::remove(&self.shared, &did).await {
            tracing::warn!(?error, did = %did.0, "verification index cleanup failed after finish");
        }
        Ok(written)
    }

    /// Already handed off: the mode is not re-registered through this line.
    async fn hand_off_verification(
        &mut self,
        _draft: VerificationHandoffDraft,
    ) -> Result<IdempotentWrite, HolderError> {
        Err(HolderError::InvalidTransition {
            code: "verification_mode".into(),
        })
    }

    /// Records the durable `Quarantined` state (plan T7): first write wins,
    /// repeats are idempotent, and the phase is never moved by it.
    async fn record_quarantine(
        &mut self,
        at: TimestampMillis,
        reason: &str,
    ) -> Result<IdempotentWrite, HolderError> {
        if reason.is_empty() || reason.len() > QUARANTINE_REASON_MAX {
            return invalid("quarantine_reason");
        }
        let record = QuarantineRecord { at, reason: reason.to_owned() };
        self.transition(Some(IdempotentWrite::Applied), |doc| {
            let execution = &doc.view.execution;
            if execution.quarantined.is_some() {
                return Ok(Step::Keep(IdempotentWrite::AlreadyApplied));
            }
            if execution.phase == DispatchPhase::Ended {
                return invalid("quarantine_ended_dispatch");
            }
            let mut next = execution.clone();
            next.quarantined = Some(record.clone());
            Ok(Step::Write {
                execution: Box::new(next),
                connector: doc.connector.clone(),
                due: None,
                answer: IdempotentWrite::Applied,
            })
        })
        .await
    }
}
