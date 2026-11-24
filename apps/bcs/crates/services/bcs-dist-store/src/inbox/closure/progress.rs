use super::*;
use std::{num::NonZeroU32, ops::Bound};
#[async_trait]
impl AdmissionClosurePort for StoreAdmissionControl {
    async fn advance_closure(
        &self,
        owner: &SessionOwnership,
        closure: &AdmissionClosure,
    ) -> Result<ClosureWork, StoreError> {
        let (_, schedule) = self.checked_schedule(owner, closure).await?;
        let layout = &self.store.inner.layout;
        let certificate_key = layout.closure_certificate(&owner.sid, &closure.operation_id)?;
        if let Some((_, certificate)) = self
            .store
            .load::<ClosureCertificate>(&certificate_key)
            .await?
        {
            if certificate.closure != *closure {
                return Err(invalid("closure_certificate_conflict"));
            }
            return Ok(ClosureWork {
                closure: closure.clone(),
                completed_through: closure.journal_cut,
                obligation: None,
                certificate: Some(certificate),
            });
        }
        let key = layout.closure_progress(&owner.sid, &closure.operation_id)?;
        let (version, mut progress) = if let Some((version, progress)) =
            self.store.load::<Progress>(&key).await?
        {
            (Some(version), progress)
        } else {
            let (_, envelope) = self
                .store
                .load::<CheckpointEnvelope>(&layout.ckpt(&owner.sid)?)
                .await?
                .ok_or_else(|| invalid("closure_checkpoint_missing"))?;
            if envelope.checkpoint.format_version != 1 || envelope.checkpoint.tasks.len() > 128 {
                return Err(invalid("closure_checkpoint_format"));
            }
            let checkpoint_tasks = envelope
                .checkpoint
                .tasks
                .iter()
                .map(|t| t.tid.clone())
                .collect();
            (
                None,
                Progress {
                    closure: closure.clone(),
                    cursor: 0,
                    pending: None,
                    checkpoint_tasks,
                    checkpoint_cursor: 0,
                    inbox: None,
                    terminal_tasks: 0,
                    evidence_digest: crate::identity::digest_json(
                        &serde_json::to_value(closure).map_err(|_| invalid("closure_encoding"))?,
                    ),
                },
            )
        };
        if progress.closure != *closure {
            return Err(invalid("closure_progress_conflict"));
        }
        progress.validate()?;
        if version.is_none() {
            self.store
                .save(&key, None, &progress, &closure.operation_id.0)
                .await?;
            return Ok(progress.work(None));
        }
        if progress.pending.is_some() {
            return self
                .advance_pending(owner, &key, version, &mut progress)
                .await;
        }
        if let Some(tid) = progress.checkpoint_tasks.get(progress.checkpoint_cursor) {
            let (_, task) = self
                .store
                .load::<TaskSnapshot>(&layout.task(tid)?)
                .await?
                .ok_or_else(|| invalid("closure_task_missing"))?;
            if task.sid != owner.sid {
                return Err(invalid("closure_task_scope"));
            }
            let Some(evidence) = self.evidence(&task.scope, tid).await? else {
                return Ok(progress.work(None));
            };
            progress.confirmed(&evidence)?;
            progress.checkpoint_cursor += 1;
            self.store
                .save(&key, version, &progress, &closure.operation_id.0)
                .await?;
            return Ok(progress.work(None));
        }
        if let Some((admission_key, slot)) = schedule
            .slots
            .iter()
            .find(|(_, slot)| slot.start && slot.generation == closure.generation)
        {
            let obligation = self.finish_slot(owner, closure, admission_key, slot).await?;
            return Ok(progress.work(obligation));
        }
        if progress.inbox.is_none() {
            // Closing the gate freezes the accepted Starts for this generation.
            // Capture the Inbox tail once so later control or agent events cannot
            // keep the closure scan alive. Skip the already consumed prefix.
            let through = self
                .store
                .checkpoint(&owner.sid)
                .await?
                .ok_or_else(|| invalid("closure_checkpoint_missing"))?
                .checkpoint
                .consumed;
            let start = self.inbox_start(&owner.sid, through.as_ref())?;
            let cut = self
                .store
                .read_inputs(&owner.sid, start, Bound::Unbounded, NonZeroU32::MIN, true)
                .await?
                .first()
                .map(|row| self.store.position(&owner.sid, row.id));
            progress.inbox = Some(InboxScan { through, cut });
            if !self.store.save(&key, version, &progress, &closure.operation_id.0).await? {
                return Err(unknown(&closure.operation_id.0));
            }
            return Ok(progress.work(None));
        }
        if let Some(scan) = &progress.inbox
            && scan.cut.is_some()
            && scan.through != scan.cut
        {
            return self.advance_inbox(owner, &key, version, &mut progress).await;
        }
        // Recheck the checkpoint at the completion boundary. No new ordinary
        // task can enter under isolation; independent finalization may advance it.
        let (_, envelope) = self
            .store
            .load::<CheckpointEnvelope>(&layout.ckpt(&owner.sid)?)
            .await?
            .ok_or_else(|| invalid("closure_checkpoint_missing"))?;
        if envelope.checkpoint.format_version != 1 || envelope.checkpoint.tasks.len() > 128 {
            return Err(invalid("closure_checkpoint_format"));
        }
        let mut added = false;
        for task in &envelope.checkpoint.tasks {
            if !progress.checkpoint_tasks.contains(&task.tid) {
                progress.checkpoint_tasks.push(task.tid.clone());
                added = true;
            }
        }
        if added {
            progress.validate()?;
            self.store
                .save(&key, version, &progress, &closure.operation_id.0)
                .await?;
            return Ok(progress.work(None));
        }
        if envelope
            .checkpoint
            .tasks
            .iter()
            .any(|task| task.phase != TaskPhase::Done)
        {
            return Ok(progress.work(None));
        }
        self.checked_schedule(owner, closure).await?;
        let certificate = ClosureCertificate {
            closure: closure.clone(),
            completed_through: progress.cursor,
            terminal_tasks: progress.terminal_tasks,
            evidence_digest: progress.evidence_digest.clone(),
        };
        if !self
            .store
            .save(
                &certificate_key,
                None,
                &certificate,
                &closure.operation_id.0,
            )
            .await?
        {
            let stored = self
                .store
                .load::<ClosureCertificate>(&certificate_key)
                .await?
                .ok_or_else(|| unknown(&closure.operation_id.0))?;
            if stored.1 != certificate {
                return Err(invalid("closure_certificate_conflict"));
            }
        }
        Ok(ClosureWork {
            certificate: Some(certificate),
            ..progress.work(None)
        })
    }
    async fn reopen_gate(
        &self,
        guard: ControlGuard,
        request_id: RequestId,
    ) -> Result<GateReopenOutcome, StoreError> {
        self.reopen(guard, request_id).await
    }
}
impl StoreAdmissionControl {
    async fn advance_pending(
        &self,
        owner: &SessionOwnership,
        key: &str,
        version: Option<bcs_store_api::Version>,
        progress: &mut Progress,
    ) -> Result<ClosureWork, StoreError> {
        let pending = progress
            .pending
            .as_mut()
            .ok_or_else(|| invalid("closure_pending_missing"))?;
        let task = pending
            .obligation
            .request
            .create_task
            .as_ref()
            .ok_or_else(|| invalid("closure_initial_task_missing"))?;
        if task.sid != owner.sid {
            return Err(invalid("closure_task_scope"));
        }
        match pending.phase {
            Materialize::Spec => {
                if let Some(spec) = &pending.obligation.request.create_session {
                    let key = self.store.inner.layout.spec(&spec.sid)?;
                    if !self
                        .store
                        .save(&key, None, spec, &progress.closure.operation_id.0)
                        .await?
                    {
                        let (_, stored) = self
                            .store
                            .load::<SessionSpec>(&key)
                            .await?
                            .ok_or_else(|| unknown("closure_spec"))?;
                        if stored.scope != spec.scope || stored.group != spec.group {
                            return Err(invalid("closure_spec_conflict"));
                        }
                    }
                }
                pending.phase = Materialize::ScopeIndex;
            }
            Materialize::ScopeIndex | Materialize::SessionIndex => {
                let key = if matches!(pending.phase, Materialize::ScopeIndex) {
                    self.store.inner.layout.tasks(&task.scope)?
                } else {
                    self.store.inner.layout.session_tasks(&task.sid)?
                };
                let _permit = self.store.io_permit().await?;
                tokio::time::timeout(
                    std::time::Duration::from_millis(500),
                    self.store.inner.zset.add(
                        &key,
                        &[ZSetEntry {
                            member: task.tid.0.clone(),
                            score: 0.0,
                        }],
                        ZAddMode::InsertOnly,
                    ),
                )
                .await
                .map_err(|_| unknown("closure_index"))?
                .map_err(|e| crate::error::from_write(&e, "closure_index"))?;
                pending.phase = if matches!(pending.phase, Materialize::ScopeIndex) {
                    Materialize::SessionIndex
                } else {
                    Materialize::Task
                };
            }
            Materialize::Task => {
                let key = self.store.inner.layout.task(&task.tid)?;
                if !self
                    .store
                    .save(&key, None, task, &progress.closure.operation_id.0)
                    .await?
                {
                    let (_, stored) = self
                        .store
                        .load::<TaskSnapshot>(&key)
                        .await?
                        .ok_or_else(|| unknown("closure_task"))?;
                    if stored.scope != task.scope
                        || stored.sid != task.sid
                        || stored.tid != task.tid
                    {
                        return Err(invalid("closure_task_conflict"));
                    }
                }
                pending.phase = Materialize::Verify;
            }
            Materialize::Verify => {
                let Some(evidence) = self.evidence(&task.scope, &task.tid).await? else {
                    let obligation = pending.obligation.clone();
                    return Ok(progress.work(Some(obligation)));
                };
                if evidence.sid != owner.sid {
                    return Err(invalid("closure_evidence_scope"));
                }
                let sequence = pending.sequence;
                progress.confirmed(&evidence)?;
                progress.cursor = sequence;
                progress.pending = None;
            }
        }
        if !self
            .store
            .save(key, version, progress, &progress.closure.operation_id.0)
            .await?
        {
            return Err(unknown(&progress.closure.operation_id.0));
        }
        Ok(progress.work(None))
    }
    fn inbox_start(
        &self,
        sid: &Sid,
        through: Option<&InboxPos>,
    ) -> Result<Bound<bcs_store_api::stream::StreamId>, StoreError> {
        through
            .map(|pos| self.store.cursor(sid, pos).map(Bound::Excluded))
            .transpose()
            .map(|start| start.unwrap_or(Bound::Unbounded))
    }
    async fn advance_inbox(
        &self,
        owner: &SessionOwnership,
        key: &str,
        version: Option<bcs_store_api::Version>,
        progress: &mut Progress,
    ) -> Result<ClosureWork, StoreError> {
        let scan = progress.inbox.as_mut().ok_or_else(|| invalid("closure_inbox_scan"))?;
        let cut = scan.cut.as_ref().ok_or_else(|| invalid("closure_inbox_cut"))?;
        let end = Bound::Included(self.store.cursor(&owner.sid, cut)?);
        let start = self.inbox_start(&owner.sid, scan.through.as_ref())?;
        let mut rows = self.store.read_inputs(&owner.sid, start, end, NonZeroU32::MIN, false).await?;
        let row = rows.pop().ok_or_else(|| invalid("closure_inbox_gap"))?;
        let position = self.store.position(&owner.sid, row.id);
        let mut terminal = None;
        if let Some(key) = row.fields.get("admission") {
            let input = crate::codec::decode_inbox_entry(&owner.sid, &row.fields)?;
            let admission = self.store.admission_for_input(&owner.sid, &input, key).await?
                .ok_or_else(|| invalid("closure_admission_missing"))?;
            if admission.decision == Some(Decision::Accepted)
                && admission.generation == progress.closure.generation
                && admission.position.as_ref() == Some(&position)
                && let Some(task) = &admission.request.create_task
                && !progress.checkpoint_tasks.contains(&task.tid)
            {
                let Some(evidence) = self.evidence(&task.scope, &task.tid).await? else {
                    return Ok(progress.work(Some(Self::obligation(admission))));
                };
                if evidence.sid != owner.sid {
                    return Err(invalid("closure_evidence_scope"));
                }
                terminal = Some(evidence);
            }
        }
        // Also advance over physical duplicates and requests that never became
        // Accepted. The CAS persists coverage independently of Worker lifetime.
        scan.through = Some(position);
        if let Some(evidence) = terminal {
            progress.confirmed(&evidence)?;
        }
        if !self.store.save(key, version, progress, &progress.closure.operation_id.0).await? {
            return Err(unknown(&progress.closure.operation_id.0));
        }
        Ok(progress.work(None))
    }
    fn obligation(admission: AdmissionRecord) -> AdmissionObligation {
        AdmissionObligation {
            request: admission.request,
            accepted: true,
            may_have_created_task: true,
        }
    }
    async fn finish_slot(
        &self,
        owner: &SessionOwnership,
        closure: &AdmissionClosure,
        key: &str,
        slot: &Slot,
    ) -> Result<Option<AdmissionObligation>, StoreError> {
        let (admission_version, mut admission) = self.store.admission_record(key).await?
            .ok_or_else(|| invalid("closure_admission_missing"))?;
        if admission.sid() != &owner.sid || admission.generation != closure.generation || !admission.start() {
            return Err(invalid("closure_slot_identity"));
        }
        // No Start can switch to Accepted after the close CAS. Preserve an
        // already accepted slot's decision before releasing it; otherwise the
        // caller's retry could mistake accepted work for a rejected Start.
        let decision = slot.decision.clone().unwrap_or(Decision::Rejected);
        if let Some(stored) = &admission.decision {
            if *stored != decision {
                return Err(invalid("closure_slot_decision"));
            }
        } else {
            admission.decision = Some(decision.clone());
            self.store.save(key, Some(admission_version), &admission, &closure.operation_id.0).await?;
            return Ok(None);
        }
        if decision == Decision::Accepted {
            let task = admission.request.create_task.as_ref().ok_or_else(|| invalid("closure_initial_task_missing"))?;
            let Some(evidence) = self.evidence(&task.scope, &task.tid).await? else {
                return Ok(Some(Self::obligation(admission)));
            };
            if evidence.sid != owner.sid {
                return Err(invalid("closure_evidence_scope"));
            }
        }
        let (version, mut schedule) = self.checked_schedule(owner, closure).await?;
        if schedule.slots.get(key).is_some_and(|slot| slot.generation == closure.generation && slot.start) {
            schedule.slots.remove(key);
            self.store.save(&self.store.inner.layout.schedule(&owner.sid)?, version, &schedule, &closure.operation_id.0).await?;
        }
        Ok(None)
    }
}
