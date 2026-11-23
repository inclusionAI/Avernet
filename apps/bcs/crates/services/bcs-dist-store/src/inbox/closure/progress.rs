use super::*;
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
        if let Some((admission_key, _)) = schedule
            .slots
            .iter()
            .find(|(_, slot)| slot.start && slot.generation == closure.generation)
        {
            self.release_verified_slot(owner, closure, admission_key)
                .await?;
            return Ok(progress.work(None));
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
    async fn release_verified_slot(
        &self,
        owner: &SessionOwnership,
        closure: &AdmissionClosure,
        key: &str,
    ) -> Result<(), StoreError> {
        let (_, admission) = self
            .store
            .admission_record(key)
            .await?
            .ok_or_else(|| invalid("closure_admission_missing"))?;
        if admission.sid() != &owner.sid
            || admission.generation != closure.generation
            || !admission.start()
        {
            return Err(invalid("closure_slot_identity"));
        }
        let task = admission
            .request
            .create_task
            .as_ref()
            .ok_or_else(|| invalid("closure_initial_task_missing"))?;
        let Some(evidence) = self.evidence(&task.scope, &task.tid).await? else {
            return Ok(());
        };
        if evidence.sid != owner.sid {
            return Err(invalid("closure_evidence_scope"));
        }
        let (version, mut schedule) = self.checked_schedule(owner, closure).await?;
        if schedule
            .slots
            .get(key)
            .is_some_and(|slot| slot.generation == closure.generation && slot.start)
        {
            schedule.slots.remove(key);
            self.store
                .save(
                    &self.store.inner.layout.schedule(&owner.sid)?,
                    version,
                    &schedule,
                    &closure.operation_id.0,
                )
                .await?;
        }
        Ok(())
    }
}
