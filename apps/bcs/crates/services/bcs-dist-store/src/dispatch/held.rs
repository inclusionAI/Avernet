use super::*;
pub(super) struct Held {
    pub store: StoreDispatch,
    pub holder: Holder,
    pub view: DispatchView,
}
impl Held {
    async fn load(&self) -> Result<Versioned<Document>, HolderError> {
        let stored = self
            .store
            .document(&self.view.spec.did)
            .await
            .map_err(HolderError::Store)?
            .ok_or(HolderError::Lost)?;
        if stored.value.view.execution.holder.as_ref() != Some(&self.holder) {
            return Err(HolderError::Lost);
        }
        match self
            .store
            .live(&self.holder.instance)
            .await
            .map_err(HolderError::Store)?
        {
            Liveness::Alive => {}
            Liveness::Dead => return Err(HolderError::Lost),
            Liveness::Unknown => {
                return Err(HolderError::Store(unavailable("dispatch_holder_unknown")));
            }
        }
        Ok(stored)
    }
    async fn update<T>(
        &mut self,
        mut f: impl FnMut(&mut Document) -> Result<T, HolderError>,
    ) -> Result<T, HolderError> {
        let key = self
            .store
            .layout
            .disp(&self.view.spec.did)
            .map_err(HolderError::Store)?;
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await.map_err(HolderError::Store)?;
            let mut stored = tokio::time::timeout(budget.remaining(), self.load())
                .await
                .map_err(|_| HolderError::Store(unknown(&key)))??;
            let result = f(&mut stored.value)?;
            stored.value.due = Some(TimestampMillis(0));
            if budget
                .io(
                    &key,
                    self.store.write(&key, Some(stored.version), &stored.value),
                )
                .await
                .map_err(HolderError::Store)?
            {
                self.view = stored.value.view;
                return Ok(result);
            }
        }
    }
}
#[async_trait]
impl HeldDispatchPort for Held {
    fn view(&self) -> &DispatchView {
        &self.view
    }
    fn holder(&self) -> &Holder {
        &self.holder
    }
    async fn refresh(&mut self) -> Result<DispatchView, HolderError> {
        self.view = self.load().await?.value.view;
        Ok(self.view.clone())
    }
    async fn begin_send(&mut self, operation: SendOperation) -> Result<SendPermit, HolderError> {
        let (key, request) = operation_key(&operation);
        let request = request.clone();
        if request.0.is_empty()
            || request.0.len() > 256
            || bytes(&request).map_err(HolderError::Store)? > 512
        {
            return Err(transition("send_request"));
        }
        self.update(|doc| {
            if doc.ended() {
                return Err(transition("send_after_end"));
            }
            if let Some(old) = doc.sends.get(&key) {
                if old.remote.is_some() {
                    return Err(transition("send_already_accepted"));
                }
                return if old.request == request {
                    Ok(())
                } else {
                    Err(transition("send_identity_conflict"))
                };
            }
            match &operation {
                SendOperation::Start { .. } => {
                    if !matches!(
                        doc.view.execution.phase,
                        DispatchPhase::Claimed | DispatchPhase::Sending
                    ) {
                        return Err(transition("start_phase"));
                    }
                    doc.view.execution.phase = DispatchPhase::Sending;
                }
                SendOperation::Input { seq, .. } => {
                    if doc.view.execution.phase != DispatchPhase::Accepted
                        || seq.0
                            != doc
                                .view
                                .execution
                                .confirmed_input_through
                                .map_or(1, |s| s.0.saturating_add(1))
                        || !doc.view.control.pending_input.iter().any(|p| p.seq == *seq)
                    {
                        return Err(transition("input_prefix"));
                    }
                }
            }
            doc.sends.insert(
                key.clone(),
                Send {
                    request: request.clone(),
                    remote: None,
                },
            );
            Ok(())
        })
        .await?;
        Ok(SendPermit {
            did: self.view.spec.did.clone(),
            holder: self.holder.clone(),
            operation,
        })
    }
    async fn record_acceptance(
        &mut self,
        evidence: AcceptanceEvidence,
    ) -> Result<Option<CancelIntent>, HolderError> {
        if !matches!(evidence.operation, SendOperation::Start { .. }) {
            return Err(transition("acceptance_start_only"));
        }
        self.update(|doc| {
            if doc.ended() {
                return Err(transition("acceptance_after_end"));
            }
            accept(doc, &evidence)?;
            doc.view.execution.remote = Some(evidence.remote.clone());
            doc.view.execution.phase = DispatchPhase::Accepted;
            Ok(doc.view.control.cancel.clone())
        })
        .await
    }
    async fn confirm_input(
        &mut self,
        seq: InputSeq,
        evidence: AcceptanceEvidence,
    ) -> Result<IdempotentWrite, HolderError> {
        if !matches!(evidence.operation,SendOperation::Input{seq:s,..}if s==seq) {
            return Err(transition("input_evidence_seq"));
        }
        self.update(|doc| {
            let (key, request) = operation_key(&evidence.operation);
            if doc
                .view
                .execution
                .confirmed_input_through
                .is_some_and(|through| seq.0 <= through.0)
            {
                return if doc.sends.get(&key).is_some_and(|s| {
                    s.request == *request && s.remote.as_ref() == Some(&evidence.remote)
                }) {
                    Ok(IdempotentWrite::AlreadyApplied)
                } else {
                    Err(transition("input_confirmation_conflict"))
                };
            }
            if doc.ended()
                || doc.view.execution.phase != DispatchPhase::Accepted
                || seq.0
                    != doc
                        .view
                        .execution
                        .confirmed_input_through
                        .map_or(1, |s| s.0.saturating_add(1))
            {
                return Err(transition("input_prefix"));
            }
            if !doc.view.control.pending_input.iter().any(|p| p.seq == seq) {
                return Err(transition("input_missing"));
            }
            if doc.view.execution.remote.as_ref() != Some(&evidence.remote) {
                return Err(transition("input_remote_identity"));
            }
            accept(doc, &evidence)?;
            doc.view.execution.confirmed_input_through = Some(seq);
            doc.view.control.pending_input.retain(|p| p.seq != seq);
            Ok(IdempotentWrite::Applied)
        })
        .await
    }
    async fn record_cancel(
        &mut self,
        observation: CancelObservation,
    ) -> Result<IdempotentWrite, HolderError> {
        self.update(|doc| {
            if doc.view.control.cancel.is_none() {
                return Err(transition("cancel_without_intent"));
            }
            if doc.view.execution.cancellation == observation {
                return Ok(IdempotentWrite::AlreadyApplied);
            }
            if doc.ended()
                || matches!(
                    doc.view.execution.cancellation,
                    CancelObservation::ConfirmedCanceled
                        | CancelObservation::AlreadyFinished { .. }
                )
            {
                return Err(transition("cancel_after_definitive"));
            }
            if let CancelObservation::Rejected { code } = &observation
                && code.len() > 256
            {
                return Err(transition("cancel_observation_size"));
            }
            doc.view.execution.cancellation = observation.clone();
            Ok(IdempotentWrite::Applied)
        })
        .await
    }
    async fn finish(
        &mut self,
        outcome: FinalDispatchOutcome,
        evidence: String,
    ) -> Result<IdempotentWrite, HolderError> {
        if evidence.is_empty() || bytes(&evidence).map_err(HolderError::Store)? > 4096 {
            return Err(transition("terminal_evidence"));
        }
        self.update(|doc| {
            if doc.ended() {
                return Ok(
                    if doc.final_outcome() == Some(outcome)
                        && doc.terminal_evidence.as_ref() == Some(&evidence)
                    {
                        IdempotentWrite::AlreadyApplied
                    } else {
                        IdempotentWrite::ContentConflict
                    },
                );
            }
            doc.view.execution.phase = DispatchPhase::Ended;
            doc.view.execution.outcome = Some(match outcome {
                FinalDispatchOutcome::Completed => DispatchOutcome::Completed,
                FinalDispatchOutcome::Failed => DispatchOutcome::Failed,
                FinalDispatchOutcome::Canceled => DispatchOutcome::Canceled,
            });
            doc.terminal_evidence = Some(evidence.clone());
            if doc.view.control.cancel.is_some() {
                doc.view.execution.cancellation = CancelObservation::AlreadyFinished { outcome };
            }
            Ok(IdempotentWrite::Applied)
        })
        .await
    }
}
fn accept(doc: &mut Document, evidence: &AcceptanceEvidence) -> Result<(), HolderError> {
    if bytes(&evidence.remote).map_err(HolderError::Store)? > 4096
        || evidence.remote.task_id.is_empty()
        || evidence.remote.task_id.len() > 1024
        || evidence
            .remote
            .context_id
            .as_ref()
            .is_some_and(|s| s.len() > 1024)
    {
        return Err(transition("remote_identity"));
    }
    let (key, request) = operation_key(&evidence.operation);
    let send = doc
        .sends
        .get_mut(&key)
        .ok_or_else(|| transition("send_intent_missing"))?;
    if send.request != *request
        || send
            .remote
            .as_ref()
            .is_some_and(|old| old != &evidence.remote)
    {
        return Err(transition("send_evidence_conflict"));
    }
    send.remote = Some(evidence.remote.clone());
    Ok(())
}
fn transition(code: &str) -> HolderError {
    HolderError::InvalidTransition { code: code.into() }
}
