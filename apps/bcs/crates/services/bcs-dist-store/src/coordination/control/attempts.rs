use super::*;
use async_trait::async_trait;
#[async_trait]
impl SessionControlPort for CoordinationStore {
    async fn acquire_control(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<ControlAcquireOutcome, StoreError> {
        self.control_acquire(sid, claim).await
    }
    async fn load_control(&self, sid: &Sid) -> Result<Option<ControlSnapshot>, StoreError> {
        budget("load_control", async {
            Ok(self.control_header(sid).await?.map(|h| h.snapshot))
        })
        .await
    }
    async fn get_isolation(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<IsolationRecord>, StoreError> {
        self.control_get_isolation(sid, id).await
    }
    async fn get_attempt(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<RiskAttempt>, StoreError> {
        validate_text(&id.0)?;
        budget(&id.0, async {
            if let Some(header) = self.control_header(sid).await?
                && let Some(attempt) = header
                    .snapshot
                    .control
                    .active_attempt
                    .iter()
                    .chain(header.snapshot.control.suspended_runner.iter())
                    .chain(header.snapshot.control.completed_replay.iter())
                    .find(|attempt| attempt.identity.id == *id)
            {
                return Ok(Some(attempt.clone()));
            }
            Ok(self
                .read::<RiskAttempt>(&self.layout.control_attempt(sid, id)?)
                .await?
                .map(|r| r.value))
        })
        .await
    }
    async fn begin_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_begin_attempt(guard, attempt, None).await
    }
    async fn begin_replay_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        effect: EffectId,
        content_digest: Digest,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_begin_attempt(
            guard,
            attempt,
            Some(ReplayIdentity {
                effect,
                content_digest,
            }),
        )
        .await
    }
    async fn finish_attempt(
        &self,
        guard: ControlGuard,
        id: RequestId,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError> {
        validate_text(&id.0)?;
        match &outcome {
            AttemptOutcome::Succeeded { decision_digest } => validate_text(&decision_digest.0)?,
            AttemptOutcome::Transient { code } => validate_text(code)?,
            AttemptOutcome::Deterministic {
                code, isolation_id, ..
            }
            | AttemptOutcome::Uninterpretable { code, isolation_id } => {
                validate_text(code)?;
                validate_text(&isolation_id.0)?;
            }
        }
        budget(&id.0, async {
            for retry in 0..8 {
                backoff(retry).await?;
                let Some(mut header) = self.control_header(&guard.owner.sid).await? else {
                    return Ok(ControlOutcome::Fenced);
                };
                if let Some(result) = Self::check_control(&header, &guard) {
                    return Ok(result);
                }
                if let Some(completed) = &header.snapshot.control.completed_replay
                    && completed.identity.id == id
                {
                    return Ok(if completed.identity.head != guard.expected_head {
                        ControlOutcome::HeadConflict
                    } else if completed.outcome.as_ref() == Some(&outcome) {
                        ControlOutcome::AlreadyApplied(header.snapshot)
                    } else {
                        ControlOutcome::ContentConflict
                    });
                }
                let Some(active) = header
                    .snapshot
                    .control
                    .active_attempt
                    .as_ref()
                    .filter(|a| a.identity.id == id)
                else {
                    return Ok(
                        match self
                            .read::<RiskAttempt>(
                                &self.layout.control_attempt(&guard.owner.sid, &id)?,
                            )
                            .await?
                        {
                            Some(previous)
                                if previous.value.identity.head != guard.expected_head =>
                            {
                                ControlOutcome::HeadConflict
                            }
                            Some(previous) if previous.value.outcome.as_ref() == Some(&outcome) => {
                                ControlOutcome::AlreadyApplied(header.snapshot)
                            }
                            Some(_) => ControlOutcome::ContentConflict,
                            None => ControlOutcome::NotReady,
                        },
                    );
                };
                if active.identity.head != guard.expected_head {
                    return Ok(ControlOutcome::HeadConflict);
                }
                if let Some(previous) = &active.outcome {
                    return Ok(if *previous == outcome {
                        ControlOutcome::AlreadyApplied(header.snapshot)
                    } else {
                        ControlOutcome::ContentConflict
                    });
                }
                if let Some(result) = Self::check_revision(&header, &guard) {
                    return Ok(result);
                }
                let next_mode = match &outcome {
                    AttemptOutcome::Deterministic {
                        code, isolation_id, ..
                    }
                    | AttemptOutcome::Uninterpretable {
                        code, isolation_id, ..
                    } => {
                        if isolation(&header.snapshot.control.mode)
                            .is_some_and(|current| current.operation_id == *isolation_id)
                            || self
                                .read::<Value>(
                                    &self
                                        .layout
                                        .control_isolation(&guard.owner.sid, isolation_id)?,
                                )
                                .await?
                                .is_some()
                        {
                            return Ok(ControlOutcome::ContentConflict);
                        }
                        let safe = matches!(
                            &outcome,
                            AttemptOutcome::Deterministic {
                                safe_to_finalize: true,
                                ..
                            }
                        ) && serde_json::from_value::<Checkpoint>(
                            header.raw["checkpoint"].clone(),
                        )
                        .ok()
                        .is_some_and(|c| c.format_version == Checkpoint::FORMAT_VERSION);
                        let isolation = IsolationRecord {
                            operation_id: isolation_id.clone(),
                            reason: code.clone(),
                            head: guard.expected_head,
                            failed_attempt: id.clone(),
                            closure: None,
                            completion: None,
                        };
                        Some(if safe {
                            ControlMode::Quarantined { isolation }
                        } else {
                            ControlMode::Blocked { isolation }
                        })
                    }
                    _ => None,
                };
                if next_mode.is_some() {
                    self.retain_isolation(&header).await?;
                }
                if let Some(active) = header.snapshot.control.active_attempt.as_mut() {
                    active.outcome = Some(outcome.clone());
                }
                if let Some(mode) = next_mode {
                    header.snapshot.control.mode = mode;
                } else if header
                    .snapshot
                    .control
                    .active_attempt
                    .as_ref()
                    .is_some_and(|attempt| attempt.replay.is_some())
                    && header.snapshot.control.suspended_runner.is_some()
                {
                    // Only archive an already confirmed previous receipt. The
                    // new outcome and Runner restoration share this control CAS.
                    if let Some(previous) = &header.snapshot.control.completed_replay {
                        self.retain_attempt(&guard.owner.sid, previous).await?;
                    }
                    header.snapshot.control.completed_replay =
                        header.snapshot.control.active_attempt.take();
                    header.snapshot.control.active_attempt =
                        header.snapshot.control.suspended_runner.take();
                }
                if self.save_control(&mut header, &id.0).await? {
                    return Ok(ControlOutcome::Applied(header.snapshot));
                }
            }
            Err(unknown(&id.0))
        })
        .await
    }
    async fn isolate(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_isolate(guard, attempt, outcome).await
    }
    async fn record_closure(
        &self,
        guard: ControlGuard,
        closure: AdmissionClosure,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_record_closure(guard, closure).await
    }
    async fn record_closure_complete(
        &self,
        guard: ControlGuard,
        certificate: ClosureCertificate,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_record_complete(guard, certificate).await
    }
    async fn begin_recovery(
        &self,
        authorization: RecoveryAuthorization,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_begin_recovery(authorization).await
    }
    async fn confirm_recovery(
        &self,
        guard: ControlGuard,
        receipt: GateRecoveryReceipt,
    ) -> Result<ControlOutcome, StoreError> {
        self.control_confirm_recovery(guard, receipt).await
    }
}

impl CoordinationStore {
    async fn control_begin_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        replay: Option<ReplayIdentity>,
    ) -> Result<ControlOutcome, StoreError> {
        for value in [
            &attempt.id.0,
            &attempt.state_digest.0,
            &attempt.input_digest.0,
            &attempt.context_digest.0,
        ] {
            validate_text(value)?;
        }
        if attempt.head != guard.expected_head {
            return Err(invalid("attempt_head"));
        }
        budget(&attempt.id.0, async {
            for retry in 0..8 {
                backoff(retry).await?;
                let Some(mut header) = self.control_header(&guard.owner.sid).await? else {
                    return Ok(ControlOutcome::Fenced);
                };
                if let Some(outcome) = Self::check_control(&header, &guard) {
                    return Ok(outcome);
                }
                if let Some(replay) = &replay {
                    let checkpoint: Checkpoint = decode_json(header.raw["checkpoint"].clone())?;
                    if replay.effect.sid != guard.owner.sid
                        || replay.effect.commit != guard.expected_head
                        || !checkpoint.outbox.as_ref().is_some_and(|batch| {
                            batch.source_commit == guard.expected_head
                                && batch.effects.iter().any(|effect| {
                                    effect.id == replay.effect
                                        && effect.content_digest == replay.content_digest
                                })
                        })
                    {
                        return Err(invalid("replay_effect_identity"));
                    }
                }
                if header
                    .snapshot
                    .control
                    .suspended_runner
                    .as_ref()
                    .is_some_and(|pending| pending.identity.id == attempt.id)
                {
                    return Ok(ControlOutcome::NotReady);
                }
                if let Some(active) = header
                    .snapshot
                    .control
                    .active_attempt
                    .iter()
                    .chain(header.snapshot.control.completed_replay.iter())
                    .find(|active| active.identity.id == attempt.id)
                {
                    return Ok(if active.identity == attempt && active.replay == replay {
                        ControlOutcome::AlreadyApplied(header.snapshot)
                    } else {
                        ControlOutcome::ContentConflict
                    });
                }
                if let Some(previous) = self
                    .read::<RiskAttempt>(
                        &self.layout.control_attempt(&guard.owner.sid, &attempt.id)?,
                    )
                    .await?
                {
                    return Ok(
                        if previous.value.identity == attempt && previous.value.replay == replay {
                            ControlOutcome::AlreadyApplied(header.snapshot)
                        } else {
                            ControlOutcome::ContentConflict
                        },
                    );
                }
                if let Some(outcome) = Self::check_revision(&header, &guard) {
                    return Ok(outcome);
                }
                if !(matches!(header.snapshot.control.mode, ControlMode::Normal)
                    || (replay.is_some()
                        && matches!(
                            header.snapshot.control.mode,
                            ControlMode::Quarantined { .. }
                        )))
                {
                    return Ok(ControlOutcome::NotReady);
                }
                if let Some(prior) = &header.snapshot.control.active_attempt {
                    let same_head = prior.identity.head == guard.expected_head;
                    let runner_uncommitted = same_head
                        && prior.replay.is_none()
                        && matches!(prior.outcome, None | Some(AttemptOutcome::Succeeded { .. }));
                    if replay.is_some() && runner_uncommitted {
                        if header.snapshot.control.suspended_runner.is_some() {
                            return Ok(ControlOutcome::NotReady);
                        }
                        header.snapshot.control.suspended_runner = Some(prior.clone());
                    } else if same_head && prior.outcome.is_none() {
                        return Ok(ControlOutcome::NotReady);
                    }
                    self.retain_attempt(&guard.owner.sid, prior).await?;
                }
                header.snapshot.control.active_attempt = Some(RiskAttempt {
                    identity: attempt.clone(),
                    replay: replay.clone(),
                    outcome: None,
                });
                if self.save_control(&mut header, &attempt.id.0).await? {
                    return Ok(ControlOutcome::Applied(header.snapshot));
                }
            }
            Err(unknown(&attempt.id.0))
        })
        .await
    }
}
