use super::*;
impl CoordinationStore {
    pub(super) async fn control_isolate(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        outcome: AttemptOutcome,
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
        let (code, isolation_id) = match &outcome {
            AttemptOutcome::Deterministic {
                code, isolation_id, ..
            }
            | AttemptOutcome::Uninterpretable { code, isolation_id } => (code, isolation_id),
            _ => return Err(invalid("isolation_requires_failure")),
        };
        validate_text(code)?;
        validate_text(&isolation_id.0)?;
        let failed = RiskAttempt {
            identity: attempt.clone(),
            replay: None,
            outcome: Some(outcome.clone()),
        };
        budget(&attempt.id.0, async {
            for retry in 0..8 {
                backoff(retry).await?;
                let Some(mut header) = self.control_header(&guard.owner.sid).await? else {
                    return Ok(ControlOutcome::Fenced);
                };
                if let Some(result) = Self::check_control(&header, &guard) {
                    return Ok(result);
                }
                if let Some(prior) = header
                    .snapshot
                    .control
                    .active_attempt
                    .iter()
                    .chain(header.snapshot.control.suspended_runner.iter())
                    .chain(header.snapshot.control.completed_replay.iter())
                    .find(|prior| prior.identity.id == attempt.id)
                {
                    return Ok(if prior == &failed {
                        ControlOutcome::AlreadyApplied(header.snapshot)
                    } else {
                        ControlOutcome::ContentConflict
                    });
                }
                if let Some(prior) = self
                    .read::<RiskAttempt>(
                        &self.layout.control_attempt(&guard.owner.sid, &attempt.id)?,
                    )
                    .await?
                {
                    return Ok(if prior.value == failed {
                        ControlOutcome::AlreadyApplied(header.snapshot)
                    } else {
                        ControlOutcome::ContentConflict
                    });
                }
                if let Some(result) = Self::check_revision(&header, &guard) {
                    return Ok(result);
                }
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
                if let Some(prior) = &header.snapshot.control.active_attempt {
                    self.retain_attempt(&guard.owner.sid, prior).await?;
                }
                let safe =
                    matches!(
                        outcome,
                        AttemptOutcome::Deterministic {
                            safe_to_finalize: true,
                            ..
                        }
                    ) && serde_json::from_value::<Checkpoint>(header.raw["checkpoint"].clone())
                        .ok()
                        .is_some_and(|c| c.format_version == Checkpoint::FORMAT_VERSION);
                let isolation = IsolationRecord {
                    operation_id: isolation_id.clone(),
                    reason: code.clone(),
                    head: guard.expected_head,
                    failed_attempt: attempt.id.clone(),
                    closure: None,
                    completion: None,
                };
                self.retain_isolation(&header).await?;
                header.snapshot.control.mode = if safe {
                    ControlMode::Quarantined { isolation }
                } else {
                    ControlMode::Blocked { isolation }
                };
                header.snapshot.control.active_attempt = Some(failed.clone());
                if self.save_control(&mut header, &attempt.id.0).await? {
                    return Ok(ControlOutcome::Applied(header.snapshot));
                }
            }
            Err(unknown(&attempt.id.0))
        })
        .await
    }
}
