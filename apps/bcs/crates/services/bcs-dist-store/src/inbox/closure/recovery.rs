use super::*;
impl StoreAdmissionControl {
    pub(super) async fn reopen(
        &self,
        guard: ControlGuard,
        request_id: RequestId,
    ) -> Result<GateReopenOutcome, StoreError> {
        if request_id.0.is_empty() || request_id.0.len() > 256 {
            return Err(invalid("recovery_request_id"));
        }
        let key = self.store.inner.layout.schedule(&guard.owner.sid)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(&request_id.0).await?;
            let Some((_, raw)) = budget
                .io(
                    &request_id.0,
                    self.store.load::<serde_json::Value>(
                        &self.store.inner.layout.ckpt(&guard.owner.sid)?,
                    ),
                )
                .await?
            else {
                return Ok(GateReopenOutcome::Fenced);
            };
            if raw["owner"]
                != serde_json::to_value(&guard.owner.owner)
                    .map_err(|_| invalid("owner_encoding"))?
                || raw["checkpoint"]["commit_seq"] != guard.expected_head.0
            {
                return Ok(GateReopenOutcome::Fenced);
            }
            let control: SessionControl = crate::codec::decode_json(raw["control"].clone())?;
            let ControlMode::Recovering {
                isolation,
                authorization,
            } = &control.mode
            else {
                if matches!(control.mode, ControlMode::Normal)
                    && let Some(receipt) = control.last_recovery
                    && receipt.request_id == request_id
                    && receipt.owner == guard.owner
                    && receipt.head == guard.expected_head
                    && receipt.control_revision == guard.expected_revision
                    && receipt.control_revision.checked_add(1) == Some(control.revision)
                {
                    let (_, schedule) = budget
                        .io(&request_id.0, self.store.schedule(&guard.owner.sid))
                        .await?;
                    if schedule.owner.as_ref() == Some(&guard.owner.owner)
                        && schedule.recovered.as_ref() == Some(&receipt)
                        && schedule.closed.is_none()
                        && schedule.generation == receipt.opened_generation
                        && schedule.control_revision == receipt.control_revision
                    {
                        return Ok(GateReopenOutcome::AlreadyOpened(receipt));
                    }
                }
                return Ok(GateReopenOutcome::NotReady);
            };
            if control.revision != guard.expected_revision
                || authorization.request_id != request_id
                || authorization.guard.owner != guard.owner
                || authorization.guard.expected_head != guard.expected_head
            {
                return Ok(GateReopenOutcome::Conflict);
            }
            let closure = &authorization.completion.closure;
            let certificate = budget
                .io(
                    &request_id.0,
                    self.store.load::<ClosureCertificate>(
                        &self
                            .store
                            .inner
                            .layout
                            .closure_certificate(&guard.owner.sid, &isolation.operation_id)?,
                    ),
                )
                .await?;
            if certificate.as_ref().map(|(_, c)| c) != Some(&authorization.completion) {
                return Ok(GateReopenOutcome::NotReady);
            }
            let (version, mut schedule) = budget
                .io(&request_id.0, self.store.schedule(&guard.owner.sid))
                .await?;
            let receipt = GateRecoveryReceipt {
                sid: guard.owner.sid.clone(),
                isolation_id: isolation.operation_id.clone(),
                request_id: request_id.clone(),
                owner: guard.owner.clone(),
                head: guard.expected_head,
                control_revision: guard.expected_revision,
                closed_generation: closure.generation,
                opened_generation: closure
                    .generation
                    .checked_add(1)
                    .ok_or_else(|| invalid("generation_exhausted"))?,
            };
            if schedule.owner.as_ref() != Some(&guard.owner.owner) {
                return Ok(GateReopenOutcome::Fenced);
            }
            if schedule.control_revision > guard.expected_revision {
                return Ok(GateReopenOutcome::Conflict);
            }
            if schedule.recovered.as_ref() == Some(&receipt)
                && schedule.generation == receipt.opened_generation
                && schedule.closed.is_none()
            {
                return Ok(GateReopenOutcome::AlreadyOpened(receipt));
            }
            let completed_before_takeover = schedule.closed.is_none()
                && schedule.generation == receipt.opened_generation
                && schedule.recovered.as_ref().is_some_and(|old| {
                    old.isolation_id == receipt.isolation_id
                        && old.head == receipt.head
                        && old.closed_generation == receipt.closed_generation
                        && old.opened_generation == receipt.opened_generation
                });
            if !completed_before_takeover
                && (schedule.generation != closure.generation
                    || !schedule.closed.as_ref().is_some_and(|c| {
                        c.operation == isolation.operation_id
                            && c.generation == closure.generation
                    }))
            {
                return Ok(GateReopenOutcome::Conflict);
            }
            if schedule
                .slots
                .values()
                .any(|slot| slot.start && slot.generation == closure.generation)
            {
                return Ok(GateReopenOutcome::NotReady);
            }
            schedule.control_revision = guard.expected_revision;
            schedule.generation = receipt.opened_generation;
            schedule.closed = None;
            schedule.recovered = Some(receipt.clone());
            schedule.activation = schedule
                .activation
                .checked_add(1)
                .ok_or_else(|| invalid("activation_exhausted"))?;
            schedule.next_check = Some(self.store.inner.clock.now());
            if budget
                .io(
                    &request_id.0,
                    self.store.save(&key, version, &schedule, &request_id.0),
                )
                .await?
            {
                return Ok(GateReopenOutcome::Opened(receipt));
            }
            // Retry the original authorization; never refresh guard/revision or
            // closed generation after a concurrent new isolation/recovery.
        }
    }
}
