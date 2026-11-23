use super::*;

enum Mutation {
    Closed(AdmissionClosure),
    Completed(ClosureCertificate),
    Recover(RecoveryAuthorization),
    Confirm(GateRecoveryReceipt),
}
impl CoordinationStore {
    pub(super) async fn control_record_closure(
        &self,
        guard: ControlGuard,
        closure: AdmissionClosure,
    ) -> Result<ControlOutcome, StoreError> {
        let id = closure.operation_id.0.clone();
        self.control_mutate(guard, &id, Mutation::Closed(closure))
            .await
    }
    pub(super) async fn control_record_complete(
        &self,
        guard: ControlGuard,
        certificate: ClosureCertificate,
    ) -> Result<ControlOutcome, StoreError> {
        let id = certificate.closure.operation_id.0.clone();
        self.control_mutate(guard, &id, Mutation::Completed(certificate))
            .await
    }
    pub(super) async fn control_begin_recovery(
        &self,
        authorization: RecoveryAuthorization,
    ) -> Result<ControlOutcome, StoreError> {
        let id = authorization.request_id.0.clone();
        self.control_mutate(
            authorization.guard.clone(),
            &id,
            Mutation::Recover(authorization),
        )
        .await
    }
    pub(super) async fn control_confirm_recovery(
        &self,
        guard: ControlGuard,
        receipt: GateRecoveryReceipt,
    ) -> Result<ControlOutcome, StoreError> {
        let id = receipt.request_id.0.clone();
        self.control_mutate(guard, &id, Mutation::Confirm(receipt))
            .await
    }
    async fn control_mutate(
        &self,
        guard: ControlGuard,
        id: &str,
        mutation: Mutation,
    ) -> Result<ControlOutcome, StoreError> {
        validate_text(id)?;
        budget(id, async {
            for retry in 0..8 {
                backoff(retry).await?;
                let Some(mut header) = self.control_header(&guard.owner.sid).await? else {
                    return Ok(ControlOutcome::Fenced);
                };
                if let Some(result) = Self::check_control(&header, &guard) {
                    return Ok(result);
                }
                if let Some(result) = self.control_apply(&mut header, &guard, &mutation).await? {
                    return Ok(result);
                }
                if self.save_control(&mut header, id).await? {
                    return Ok(ControlOutcome::Applied(header.snapshot));
                }
            }
            Err(unknown(id))
        })
        .await
    }
    async fn verified_certificate(
        &self,
        certificate: &ClosureCertificate,
    ) -> Result<bool, StoreError> {
        let closure = &certificate.closure;
        Ok(self
            .read::<ClosureCertificate>(
                &self
                    .layout
                    .closure_certificate(&closure.sid, &closure.operation_id)?,
            )
            .await?
            .is_some_and(|stored| stored.value == *certificate))
    }
    async fn control_apply(
        &self,
        header: &mut Header,
        guard: &ControlGuard,
        mutation: &Mutation,
    ) -> Result<Option<ControlOutcome>, StoreError> {
        let not_ready = || Some(ControlOutcome::NotReady);
        match mutation {
            Mutation::Closed(closure) => {
                if closure.sid != guard.owner.sid {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                let Some(current) = isolation(&header.snapshot.control.mode) else {
                    return Ok(not_ready());
                };
                if current.operation_id != closure.operation_id {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if current.closure.as_ref() == Some(closure) {
                    return Ok(Some(ControlOutcome::AlreadyApplied(
                        header.snapshot.clone(),
                    )));
                }
                if current.closure.is_some() {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if let Some(result) = Self::check_revision(header, guard) {
                    return Ok(Some(result));
                }
                let Some(schedule) = self
                    .read::<Value>(&self.layout.schedule(&closure.sid)?)
                    .await?
                else {
                    return Ok(not_ready());
                };
                let closed = &schedule.value["closed"];
                if schedule.value["owner"] != encode_json(&guard.owner.owner)?
                    || closed["operation"] != encode_json(&closure.operation_id)?
                    || closed["generation"] != closure.generation
                {
                    return Ok(not_ready());
                }
                if let Some(current) = isolation_mut(&mut header.snapshot.control.mode) {
                    current.closure = Some(closure.clone());
                }
            }
            Mutation::Completed(certificate) => {
                let Some(current) = isolation(&header.snapshot.control.mode) else {
                    return Ok(not_ready());
                };
                if current.closure.as_ref() != Some(&certificate.closure) {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if current.completion.as_ref() == Some(certificate) {
                    return Ok(Some(ControlOutcome::AlreadyApplied(
                        header.snapshot.clone(),
                    )));
                }
                if let Some(result) = Self::check_revision(header, guard) {
                    return Ok(Some(result));
                }
                if !self.verified_certificate(certificate).await? {
                    return Ok(not_ready());
                }
                if let Some(current) = isolation_mut(&mut header.snapshot.control.mode) {
                    current.completion = Some(certificate.clone());
                }
            }
            Mutation::Recover(authorization) => {
                let recorded = self
                    .read::<RecoveryAuthorization>(
                        &self
                            .layout
                            .control_recovery(&guard.owner.sid, &authorization.request_id)?,
                    )
                    .await?;
                if recorded
                    .as_ref()
                    .is_some_and(|recorded| recorded.value != *authorization)
                {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if matches!(header.snapshot.control.mode, ControlMode::Normal)
                    && recorded.is_some()
                    && header
                        .snapshot
                        .control
                        .last_recovery
                        .as_ref()
                        .is_some_and(|receipt| {
                            receipt.request_id == authorization.request_id
                                && receipt.isolation_id == authorization.isolation_id
                                && receipt.owner == authorization.guard.owner
                                && receipt.head == authorization.guard.expected_head
                                && receipt.closed_generation
                                    == authorization.completion.closure.generation
                                && authorization.guard.expected_revision.checked_add(1)
                                    == Some(receipt.control_revision)
                        })
                {
                    return Ok(Some(ControlOutcome::AlreadyApplied(
                        header.snapshot.clone(),
                    )));
                }
                if let ControlMode::Recovering {
                    authorization: existing,
                    ..
                } = &header.snapshot.control.mode
                {
                    if existing == authorization {
                        return Ok(Some(ControlOutcome::AlreadyApplied(
                            header.snapshot.clone(),
                        )));
                    }
                    // A new owner needs a newly authorized identity. The old
                    // authorization is never silently refreshed on its behalf.
                    if existing.request_id == authorization.request_id
                        || existing.guard.owner == authorization.guard.owner
                    {
                        return Ok(Some(ControlOutcome::ContentConflict));
                    }
                }
                let Some(current) = isolation(&header.snapshot.control.mode) else {
                    return Ok(not_ready());
                };
                if current.operation_id != authorization.isolation_id
                    || current.completion.as_ref() != Some(&authorization.completion)
                {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if let Some(result) = Self::check_revision(header, guard) {
                    return Ok(Some(result));
                }
                if serde_json::from_value::<Checkpoint>(header.raw["checkpoint"].clone())
                    .ok()
                    .is_none_or(|c| c.format_version != 1)
                    || !self.verified_certificate(&authorization.completion).await?
                {
                    return Ok(not_ready());
                }
                let current = current.clone();
                if !self.retain_recovery(authorization).await? {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                header.snapshot.control.mode = ControlMode::Recovering {
                    isolation: current,
                    authorization: authorization.clone(),
                };
            }
            Mutation::Confirm(receipt) => {
                if matches!(header.snapshot.control.mode, ControlMode::Normal)
                    && header.snapshot.control.last_recovery.as_ref() == Some(receipt)
                {
                    return Ok(Some(ControlOutcome::AlreadyApplied(
                        header.snapshot.clone(),
                    )));
                }
                let ControlMode::Recovering {
                    isolation: current,
                    authorization,
                } = &header.snapshot.control.mode
                else {
                    return Ok(not_ready());
                };
                if receipt.sid != guard.owner.sid
                    || receipt.owner != guard.owner
                    || receipt.head != guard.expected_head
                    || receipt.control_revision != guard.expected_revision
                    || receipt.isolation_id != current.operation_id
                    || receipt.request_id != authorization.request_id
                    || receipt.closed_generation != authorization.completion.closure.generation
                    || receipt.closed_generation.checked_add(1) != Some(receipt.opened_generation)
                {
                    return Ok(Some(ControlOutcome::ContentConflict));
                }
                if let Some(result) = Self::check_revision(header, guard) {
                    return Ok(Some(result));
                }
                let Some(schedule) = self
                    .read::<Value>(&self.layout.schedule(&receipt.sid)?)
                    .await?
                else {
                    return Ok(not_ready());
                };
                if !schedule.value["closed"].is_null()
                    || schedule.value["generation"] != receipt.opened_generation
                    || schedule.value["owner"] != encode_json(&guard.owner.owner)?
                    || schedule.value["recovered"] != encode_json(receipt)?
                {
                    return Ok(not_ready());
                }
                self.retain_isolation(header).await?;
                if let Some(suspended) = &header.snapshot.control.suspended_runner {
                    self.retain_attempt(&guard.owner.sid, suspended).await?;
                }
                header.snapshot.control.suspended_runner = None;
                header.snapshot.control.mode = ControlMode::Normal;
                header.snapshot.control.last_recovery = Some(receipt.clone());
            }
        }
        Ok(None)
    }
}
